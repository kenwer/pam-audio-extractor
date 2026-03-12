#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "soundfile",
#   "gooey",
#   "six",
# ]
# ///

import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

USE_GUI = len(sys.argv) == 1 or "--ignore-gooey" in sys.argv
if USE_GUI:
    from gooey import Gooey, GooeyParser


def open_native_file_manager(path: str) -> None:
    """Open a folder in the native file manager (Finder, Explorer, Nautilus)."""
    path = os.path.normpath(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def print_version_info() -> None:
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


def default_output_dir(csv_path: str, rows: list[dict] | None = None) -> Path:
    """Return the default output directory for extracted snippets.

    Prefers placing snippets next to the source audio (derived from the ``file``
    column of the first detection row).  Falls back to the directory containing
    the detections CSV when no rows are available.
    """
    if rows:
        audio_root = Path(rows[0]["file"]).parent.parent
        return audio_root / "top-detection-snippets"
    return Path(csv_path).parent / "top-detection-snippets"


def _config_defaults(script_path: Path) -> dict:
    """Read the script's section from config.toml next to the script.

    Looks for a ``[<script-stem>]`` section, e.g. ``[3-extract-top-detections]``.
    Returns an empty dict if the file or section does not exist.
    """
    import tomllib

    if getattr(sys, "frozen", False):
        config_path = Path(sys.executable).parent / "config.toml"
    else:
        config_path = script_path.parent / "config.toml"
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f).get(script_path.stem, {})


def parse_args() -> argparse.Namespace:
    if USE_GUI:
        parser = GooeyParser(description="Extract top-N highest-confidence detection snippets per (ARU, species) pair.")
    else:
        parser = argparse.ArgumentParser(description="Extract top-N highest-confidence detection snippets per (ARU, species) pair.")

    def gui(**kw) -> dict:
        """Return kwargs only when running in GUI mode (Gooey widget hints)."""
        return kw if USE_GUI else {}

    # Pre-populate GUI form fields from config.toml on first launch (not on the
    # --ignore-gooey pass when the actual work runs).
    cfg = _config_defaults(Path(__file__)) if USE_GUI and "--ignore-gooey" not in sys.argv else {}

    required = parser.add_argument_group("Required")
    required.add_argument(
        "detections_csv",
        **({} if USE_GUI else {"nargs": "?"}),
        **gui(widget="FileChooser", gooey_options={"full_width": True}),
        default=cfg.get("detections_csv") or None,
        help="Path to detections CSV",
    )

    optional = parser.add_argument_group(
        "Optional", **({} if not USE_GUI else {"gooey_options": {"columns": 2}})
    )
    optional.add_argument(
        "--output",
        type=str,
        default=cfg.get("output") or None,
        **gui(widget="DirChooser", gooey_options={"full_width": True}),
        help="Output root directory (default: top-detection-snippets/ next to the source audio recordings)",
    )
    optional.add_argument(
        "--top-n",
        dest="top_n",
        choices=["No limit"] + [str(i) for i in range(1, 21)],
        default=str(cfg.get("top_n", 10)),
        **gui(widget="Dropdown"),
        help="Max snippets per (ARU, species) pair (default: 10)",
    )
    optional.add_argument(
        "--padding",
        type=float,
        default=cfg.get("padding", 1.5),
        **gui(widget="DecimalField"),
        help="Seconds of audio before/after detection window",
    )
    optional.add_argument(
        "--date-from",
        type=str,
        default=cfg.get("date_from") or None,
        **gui(widget="DateChooser"),
        help="Exclude recordings before this date (YYYY-MM-DD)",
    )
    optional.add_argument(
        "--date-to",
        type=str,
        default=cfg.get("date_to") or None,
        **gui(widget="DateChooser"),
        help="Exclude recordings after this date (YYYY-MM-DD)",
    )
    optional.add_argument(
        "--species-filter-file",
        dest="species_filter_file",
        default=cfg.get("species_filter_file") or None,
        **gui(widget="FileChooser", gooey_options={"full_width": True}),
        help="Path to species filter file (Scientific name_Common name, one per line)",
    )
    optional.add_argument(
        "--aru",
        action="append",
        dest="aru",
        metavar="ARU",
        **gui(widget="Textarea"),
        help="Include only these ARU numbers (repeatable; one per line in GUI)",
    )

    advanced = parser.add_argument_group("Advanced")
    advanced.add_argument(
        "--no-reveal",
        dest="no_reveal",
        action="store_true",
        default=cfg.get("no_reveal", False),
        **gui(widget="CheckBox"),
        help="Do not open the output folder in the file manager when done",
    )
    advanced.add_argument(
        "--version", action="store_true", help="Show version information and exit"
    )
    return parser.parse_args()


def load_detections(csv_path: str) -> list[dict]:
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except FileNotFoundError:
        print(f"error: detections CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"error: cannot read detections CSV (permission denied): {csv_path}", file=sys.stderr)
        sys.exit(1)
    except UnicodeDecodeError:
        print(f"error: detections CSV is not valid UTF-8: {csv_path}", file=sys.stderr)
        sys.exit(1)


def _flatten(values: list[str] | None) -> list[str] | None:
    """Normalise a list that may contain newline-joined values from a Gooey Textarea.

    CLI:  args.aru = ["109", "110"]   (two --aru flags, already flat)
    GUI:  args.aru = ["109\\n110"]    (one Textarea value with newlines)
    Both become ["109", "110"].
    """
    if not values:
        return None
    flat = [item.strip() for v in values for item in v.splitlines() if item.strip()]
    return flat or None


def load_species_filter(filter_path: str | Path) -> set[str]:
    """Load a species filter file into a set of label strings.

    Each non-blank, non-comment line is expected to be in BirdNET label format:
    ``Scientific name_Common name`` (e.g. ``Porzana porzana_Spotted Crake``).
    Lines starting with ``#`` and lines without an underscore are skipped.
    """
    species: set[str] = set()
    with open(filter_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "_" in line:
                species.add(line)
    return species


def apply_filters(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    allowed_arus = set(args.aru) if args.aru else None
    allowed_species = load_species_filter(args.species_filter_file) if args.species_filter_file else None

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date() if args.date_from else None
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d").date() if args.date_to else None

    filtered = []
    for row in rows:
        if allowed_arus and row["aru_number"] not in allowed_arus:
            continue

        if date_from or date_to:
            rec_dt = _recording_datetime(row)
            if rec_dt is None:
                print(f"Warning: Cannot determine recording date for {row.get('file', '?')!r}, skipping date filter", file=sys.stderr)
            else:
                rec_date = rec_dt.date()
                if date_from and rec_date < date_from:
                    continue
                if date_to and rec_date > date_to:
                    continue

        species_key = f"{row['scientific_name']}_{row['species']}"
        if allowed_species and species_key not in allowed_species:
            continue

        filtered.append(row)

    return filtered


def _recording_datetime(row: dict) -> datetime | None:
    """Return the recording datetime for a row.

    Tries the ``recording_time`` column first, then falls back to parsing
    the ``YYYYMMDD_HHMMSS`` pattern from the audio filename.
    """
    rec_time_str = row.get("recording_time", "").strip()
    if rec_time_str:
        try:
            return datetime.fromisoformat(rec_time_str)
        except ValueError:
            pass
    m = re.search(r"(\d{8}_\d{6})", Path(row["file"]).stem)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return None


def extract_snippet(row: dict, padding: float, out_path: Path) -> None:
    import soundfile as sf

    audio, sr = sf.read(row["file"])
    start = max(0.0, float(row["start_time"]) - padding)
    end = min(len(audio) / sr, float(row["end_time"]) + padding)
    snippet = audio[int(start * sr) : int(end * sr)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, snippet, sr)


def main() -> None:
    args = parse_args()

    if args.version:
        print_version_info()
        return

    args.aru = _flatten(args.aru)

    if not args.detections_csv:
        print("error: detections_csv is required", file=sys.stderr)
        sys.exit(1)

    if args.species_filter_file and not Path(args.species_filter_file).exists():
        print(f"error: --species-filter-file not found: {args.species_filter_file}", file=sys.stderr)
        sys.exit(1)

    top_n = None if args.top_n in (None, "No limit") else int(args.top_n)

    rows = load_detections(args.detections_csv)
    output_dir = Path(args.output) if args.output else default_output_dir(args.detections_csv, rows)
    rows = apply_filters(rows, args)

    # Group by (aru_number, species_key)
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        species_key = f"{row['scientific_name']}_{row['species']}"
        group_key = (row["aru_number"], species_key)
        groups.setdefault(group_key, []).append(row)

    total_snippets = 0
    for (aru, species_key), group_rows in groups.items():
        try:
            group_rows.sort(key=lambda r: float(r["confidence"]), reverse=True)
        except (ValueError, KeyError) as e:
            print(f"Warning: Skipping group ({aru}, {species_key}): bad confidence value: {e}", file=sys.stderr)
            continue
        top_rows = group_rows[:top_n]

        for row in top_rows:
            try:
                confidence = float(row["confidence"])
                start_t = float(row["start_time"])
                end_t = float(row["end_time"])
            except (ValueError, KeyError) as e:
                print(f"Warning: Skipping row (aru={aru}, species={species_key}): bad numeric value: {e}", file=sys.stderr)
                continue

            rec_dt = _recording_datetime(row)
            ts_str = rec_dt.strftime("%Y%m%d_%H%M%S") if rec_dt else "unknown"

            seg_rank = row.get("segment_rank", "")
            filename = f"{aru}_-_{species_key}_-_segrank{seg_rank}_-_conf{confidence:.4f}_-_{ts_str}_-_{start_t}_-_{end_t}.wav"
            out_path = output_dir / filename

            try:
                extract_snippet(row, args.padding, out_path)
                total_snippets += 1
            except Exception as e:
                print(f"Warning: Skipping {filename}: {e}", file=sys.stderr)

    print(f"Groups processed : {len(groups)}")
    print(f"Snippets written : {total_snippets}")
    print(f"Output directory : {output_dir.resolve()}")
    if not args.no_reveal:
        open_native_file_manager(str(output_dir.resolve()))


if USE_GUI:
    main = Gooey(
        program_name="Extract Top Detections",
        show_progress_bar=True,
        default_size=(800, 700),
        navigation="TABBED",
        show_stop_button=False,
        body_width=80,
    )(main)

if __name__ == "__main__":
    main()
