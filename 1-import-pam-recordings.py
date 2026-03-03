#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "psutil",
#   "gooey",
#   "six",
# ]
# ///

import argparse
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

USE_GUI = len(sys.argv) == 1 or "--ignore-gooey" in sys.argv
if USE_GUI:
    from gooey import Gooey, GooeyParser


def print_version_info() -> None:
    """Print version information for Python and installed packages."""
    print(f"Python executable:  {sys.executable}")
    print(f"Python version:     {sys.version}")
    print()
    print("Python packages installed in the current environment:")
    try:
        subprocess.run(["uv", "pip", "freeze"], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run([sys.executable, "-m", "pip", "freeze"], check=True)
        except subprocess.CalledProcessError:
            print("Warning: Unable to list installed packages", file=sys.stderr)


def _config_defaults(script_path: Path) -> dict:
    """Read the script's section from config.toml next to the script.

    Looks for a ``[<script-stem>]`` section, e.g. ``[1-import-pam-recordings]``.
    Returns an empty dict if the file or section does not exist.
    """
    import tomllib

    config_path = script_path.parent / "config.toml"
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f).get(script_path.stem, {})


def get_volume_name(partition) -> str | None:
    """Return the volume label for a disk partition, or None if unavailable.

    On macOS and Linux the volume name is the last component of the mountpoint
    (e.g. ``/media/user/MSD-110`` returns ``MSD-110``).  On Windows the mountpoint is a
    drive letter so we call ``GetVolumeInformationW`` via ctypes.
    """
    mountpoint = partition.mountpoint

    if sys.platform == "win32":
        import ctypes

        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(mountpoint),
            buf,
            ctypes.sizeof(buf),
            None,
            None,
            None,
            None,
            0,
        )
        if ok:
            return buf.value or None
        return None

    # macOS example: /Volumes/MSD-110 returns MSD-110
    # Linux example: /media/user/MSD-110 returns MSD-110
    name = Path(mountpoint).name
    return name if name else None


def get_matching_mounts(pattern: re.Pattern) -> dict:
    """Return ``{card_name: partition}`` for all mounted volumes whose name matches *pattern*."""
    result: dict = {}
    for partition in psutil.disk_partitions():
        try:
            name = get_volume_name(partition)
        except Exception:
            continue
        if name and pattern.search(name):
            result[name] = partition
    return result


def eject_card(card_name: str, partition) -> None:
    """Eject the SD card for the given partition."""
    mountpoint = partition.mountpoint
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["diskutil", "eject", mountpoint],
                check=True,
                capture_output=True,
            )
            print(f"[{card_name}] Ejected.", flush=True)
        elif sys.platform == "win32":
            import ctypes
            import ctypes.wintypes
            drive_letter = mountpoint[0]
            # Open the volume device, then lock, dismount, and eject via DeviceIoControl.
            # Should be equivalent to Explorer's "Eject".
            # See: 
            #  - https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
            #  - https://learn.microsoft.com/en-us/windows/win32/devio/device-input-and-output-control-ioctl-
            #  - https://learn.microsoft.com/en-us/windows/win32/devio/calling-deviceiocontrol
            FSCTL_LOCK_VOLUME         = 0x00090018  # winioctl.h
            FSCTL_DISMOUNT_VOLUME     = 0x00090020  # winioctl.h
            IOCTL_STORAGE_EJECT_MEDIA = 0x2D4808    # winioctl.h / ntddstor.h
            ERROR_ACCESS_DENIED       = 5           # winerror.h
            ERROR_SHARING_VIOLATION   = 32          # winerror.h
            INVALID_HANDLE_VALUE      = ctypes.c_void_p(-1).value
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateFileW(
                f"\\\\.\\{drive_letter}:",
                0x80000000,           # GENERIC_READ (winnt.h)
                0x00000003,           # FILE_SHARE_READ | FILE_SHARE_WRITE
                None,                 # lpSecurityAttributes
                3,                    # OPEN_EXISTING
                0,                    # dwFlagsAndAttributes
                None                  # hTemplateFile
            )
            if handle == INVALID_HANDLE_VALUE:
                raise OSError(f"Cannot open {drive_letter}: — {ctypes.FormatError()}")
            try:
                n = ctypes.c_uint32()
                # Retry locking: Explorer, antivirus, or the indexer may briefly hold
                # the volume after the copy finishes, causing LOCK_VOLUME to fail.
                for attempt in range(1, 11):
                    if kernel32.DeviceIoControl(handle, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(n), None):
                        break
                    err = kernel32.GetLastError()
                    if err not in (ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION) or attempt == 10:
                        raise OSError(f"FSCTL_LOCK_VOLUME failed — {ctypes.FormatError(err)}")
                    time.sleep(0.5)
                if not kernel32.DeviceIoControl(handle, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(n), None):
                    raise OSError(f"FSCTL_DISMOUNT_VOLUME failed — {ctypes.FormatError()}")
                if not kernel32.DeviceIoControl(handle, IOCTL_STORAGE_EJECT_MEDIA, None, 0, None, 0, ctypes.byref(n), None):
                    raise OSError(f"IOCTL_STORAGE_EJECT_MEDIA failed — {ctypes.FormatError()}")
            finally:
                kernel32.CloseHandle(handle)
            print(f"[{card_name}] Ejected.", flush=True)
        else: # Linux
            for cmd, label in [
                (["udisksctl", "eject", "--block-device", partition.device], "udisksctl"),
                (["eject", partition.device], "eject"),
                (["umount", mountpoint], "umount"),
            ]:
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    print(f"[{card_name}] Ejected ({label}).", flush=True)
                    break
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            else:
                print(
                    f"[{card_name}] Warning: could not eject (tried udisksctl, eject, umount).",
                    file=sys.stderr,
                    flush=True,
                )
    except subprocess.CalledProcessError as exc:
        print(f"[{card_name}] Warning: eject failed: {exc}", file=sys.stderr, flush=True)
    except FileNotFoundError as exc:
        print(f"[{card_name}] Warning: eject command not found: {exc}", file=sys.stderr, flush=True)


def copy_card(
    card_name: str,
    partition,
    target_dir: Path,
    overwrite: bool,
) -> None:
    """Copy WAV files (and CONFIG.TXT) from an SD card to ``target_dir/card_name/``."""
    mountpoint = Path(partition.mountpoint)
    dest_dir = target_dir / card_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    files_to_copy: list[Path] = []
    for f in mountpoint.iterdir():
        if f.is_file():
            name_upper = f.name.upper()
            if name_upper.endswith(".WAV") or name_upper == "CONFIG.TXT":
                files_to_copy.append(f)

    if not files_to_copy:
        print(f"[{card_name}] No WAV files found on card.", flush=True)
        return

    total = len(files_to_copy)
    print(f"[{card_name}] Copying {total} file(s) to {dest_dir}", flush=True)

    copied = 0
    skipped = 0
    total_bytes = 0
    start_time = time.monotonic()
    milestone = max(1, total // 10)

    for i, src in enumerate(sorted(files_to_copy), 1):
        dest = dest_dir / src.name
        if dest.exists() and not overwrite:
            skipped += 1
        else:
            shutil.copy2(src, dest)
            total_bytes += src.stat().st_size
            copied += 1

        if i % milestone == 0 or i == total:
            pct = int(100 * i / total)
            drive = f" {partition.mountpoint[0]}:" if sys.platform == "win32" else ""
            print(f"[{card_name}{drive}] {i}/{total} ({pct}%)", flush=True)

    elapsed = time.monotonic() - start_time
    total_mb = total_bytes / (1024 * 1024)
    print(
        f"[{card_name}] Done: {copied} copied, {skipped} skipped, "
        f"{total_mb:.1f} MB in {elapsed:.1f}s",
        flush=True,
    )


def worker(
    copy_queue: queue.Queue,
    seen: set[str],
    target_dir: Path,
    overwrite: bool,
) -> None:
    """Worker thread: dequeue cards, copy files, and eject."""
    while True:
        item = copy_queue.get()
        if item is None:  # Shutdown sentinel
            copy_queue.task_done()
            break
        card_name, partition = item
        try:
            copy_card(card_name, partition, target_dir, overwrite)
            eject_card(card_name, partition)
            # Allow re-insertion: remove from seen so the card is detected again if re-inserted.
            seen.discard(card_name)
        except Exception as exc:
            print(f"[{card_name}] Error: {exc}", file=sys.stderr, flush=True)
        finally:
            copy_queue.task_done()


def poll_sd_cards(
    copy_queue: queue.Queue,
    seen: set[str],
    stop_event: threading.Event,
    pattern: re.Pattern,
    poll_interval: float = 2.0,
) -> None:
    """Polling thread: detect new matching SD cards and enqueue them for copying."""
    print(f"Watching for SD cards matching {pattern.pattern!r}... (insert cards now)", flush=True)
    while not stop_event.is_set():
        try:
            current = get_matching_mounts(pattern)
            for card_name, partition in current.items():
                if card_name not in seen:
                    seen.add(card_name)
                    print(
                        f"[{card_name}] Detected at {partition.mountpoint} — queuing copy",
                        flush=True,
                    )
                    copy_queue.put((card_name, partition))
        except Exception as exc:
            print(f"Warning: error scanning mounts: {exc}", file=sys.stderr, flush=True)
        stop_event.wait(poll_interval)


def parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    if USE_GUI:
        parser = GooeyParser(
            description="Import recordings from PAM SD cards into the target directory."
        )
    else:
        parser = argparse.ArgumentParser(
            description="Import recordings from PAM SD cards into the target directory."
        )

    def gui(**kw) -> dict:
        """Return kwargs only when running in GUI mode (Gooey widget hints)."""
        return kw if USE_GUI else {}

    # Pre-populate GUI form fields from config.toml on first launch.
    cfg = (
        _config_defaults(Path(__file__))
        if USE_GUI and "--ignore-gooey" not in sys.argv
        else {}
    )

    settings = parser.add_argument_group(
        "Options", **({"gooey_options": {"columns": 3}} if USE_GUI else {})
    )
    settings.add_argument(
        "target_dir",
        **({} if USE_GUI else {"nargs": "?"}),
        **gui(widget="DirChooser", gooey_options={"full_width": True}),
        default=cfg.get("target_dir") or None,
        help="Destination root folder where recordings are organised into per-card subdirectories",
    )
    settings.add_argument(
        "--card-pattern",
        dest="card_pattern",
        default=cfg.get("card_pattern", "^MSD-"),
        help="Regular expression matched against the SD card volume name (case-insensitive, default: ^MSD-)",
    )
    settings.add_argument(
        "--overwrite",
        action="store_true",
        default=cfg.get("overwrite", False),
        **gui(widget="CheckBox"),
        help="Overwrite files that already exist in the destination (default: skip with warning)",
    )
    settings.add_argument(
        "--num-workers",
        dest="num_workers",
        type=int,
        default=cfg.get("num_workers", 2),
        **gui(widget="IntegerField"),
        help="Number of cards to copy concurrently (default: 2)",
    )
    advanced = parser.add_argument_group("Advanced")
    advanced.add_argument(
        "--version", action="store_true", help="Show version information and exit"
    )
    return parser.parse_args()


def main() -> None:
    """Watch for SD cards matching card_pattern and import their recordings into target_dir."""
    args = parse_args()

    if args.version:
        print_version_info()
        return

    if not args.target_dir:
        print("error: target_dir is required", file=sys.stderr)
        sys.exit(1)

    try:
        card_pattern = re.compile(args.card_pattern, re.IGNORECASE)
    except re.error as exc:
        print(f"error: invalid card-pattern {args.card_pattern!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Target directory : {target_dir}", flush=True)
    print(f"Card pattern     : {args.card_pattern}", flush=True)
    print(f"Concurrent copies: {args.num_workers}", flush=True)
    print(f"Overwrite files  : {args.overwrite}", flush=True)
    print(flush=True)

    copy_queue: queue.Queue = queue.Queue()
    seen: set[str] = set()
    stop_event = threading.Event()

    for _ in range(args.num_workers):
        threading.Thread(
            target=worker,
            args=(copy_queue, seen, target_dir, args.overwrite),
            daemon=True,
        ).start()

    # Start polling thread
    poll_thread = threading.Thread(
        target=poll_sd_cards,
        args=(copy_queue, seen, stop_event, card_pattern),
        daemon=True,
    )
    poll_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping — waiting for in-progress copies to finish...", flush=True)
        stop_event.set()
        copy_queue.join()
        print("All done.", flush=True)


if USE_GUI:
    main = Gooey(
        program_name="Import PAM Recordings",
        show_progress_bar=False,
        default_size=(800, 600),
        navigation="TABBED",
        show_stop_button=True,
        body_width=80,
        required_cols=1,
        required_rows=1,
    )(main)

if __name__ == "__main__":
    main()
