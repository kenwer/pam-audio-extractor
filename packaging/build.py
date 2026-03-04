#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
# ///
"""Build PAM Tools distributables using PyInstaller.

Creates an isolated venv per app, derives dependencies from each script's
PEP 723 header via uv export, and runs PyInstaller with the appropriate flags.

Usage:
    uv run --script packaging/build.py                          # build all apps
    uv run --script packaging/build.py 1-import-pam-recordings  # one app only
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

PACKAGING_DIR = Path(__file__).parent
ROOT_DIR = PACKAGING_DIR.parent

APPS = [
    "1-import-pam-recordings",
    "2-analyze-pam-recordings",
    "3-extract-top-detections",
]

BIRDNET_CHECKPOINT_CACHE = PACKAGING_DIR / ".birdnet-checkpoints"

BIRDNET_PREDOWNLOAD = textwrap.dedent("""
    import os, tempfile, wave, shutil, sys
    import birdnet_analyzer
    audio_dir = tempfile.mkdtemp()
    aru_dir = os.path.join(audio_dir, 'TEST-ARU')
    os.makedirs(aru_dir)
    wav = os.path.join(aru_dir, '20240101_000000.wav')
    with wave.open(wav, 'w') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(48000)
        wf.writeframes(bytes(48000 * 3 * 2))
    out = tempfile.mkdtemp()
    try:
        birdnet_analyzer.analyze(audio_dir, output=out, min_conf=0.99)
    except Exception as e:
        print(f'Note: {e}', file=sys.stderr)
    finally:
        shutil.rmtree(audio_dir, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    ckpt = os.path.join(os.path.dirname(birdnet_analyzer.__file__), 'checkpoints')
    if not os.path.isdir(ckpt) or not os.listdir(ckpt):
        print(f'ERROR: BirdNET checkpoints still missing at {ckpt}', file=sys.stderr)
        sys.exit(1)
    print(f'Checkpoints ready: {ckpt}')
""").strip()


def run(cmd: list, env: dict | None = None) -> None:
    subprocess.run(cmd, check=True, env=env)


def build(appname: str) -> None:
    script   = ROOT_DIR / f"{appname}.py"
    venv_dir = PACKAGING_DIR / f".venv-{appname}"
    is_mac   = sys.platform == "darwin"
    python   = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    print(f"\nBuilding : {appname}")
    print(f"  Platform : {sys.platform}")

    venv_env = {**os.environ, "VIRTUAL_ENV": str(venv_dir)}

    print("    Creating venv")
    run(["uv", "venv", "--python", "3.13", "--clear", venv_dir])

    print("    Installing dependencies")
    reqs = subprocess.run(
        ["uv", "export", "--script", script, "--no-hashes", "--format", "requirements.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(reqs)
        reqs_tmp = f.name
    try:
        run(["uv", "pip", "install", "--quiet", "-r", reqs_tmp, "pyinstaller", "--python", python])
    finally:
        os.unlink(reqs_tmp)

    # BirdNET checkpoints are not bundled with the package; they must exist before
    # PyInstaller runs so they can be bundled. Restored from local cache when available
    # (avoids re-downloading ~260 MB on every build).
    # NOTE: birdnet-analyzer transitively pulls in TensorFlow (~1-1.4 GB compressed).
    # tflite-runtime is preferred at runtime (BirdNET's own try/except), but TF
    # cannot be excluded without patching birdnet-analyzer.
    if appname == "2-analyze-pam-recordings":
        venv_ckpt = Path(subprocess.run(
            [python, "-c",
             "import birdnet_analyzer, os; "
             "print(os.path.join(os.path.dirname(birdnet_analyzer.__file__), 'checkpoints'))"],
            capture_output=True, text=True, check=True,
        ).stdout.strip())
        if BIRDNET_CHECKPOINT_CACHE.exists() and any(BIRDNET_CHECKPOINT_CACHE.iterdir()):
            print("    Restoring BirdNET checkpoints from cache")
            shutil.copytree(BIRDNET_CHECKPOINT_CACHE, venv_ckpt, dirs_exist_ok=True)
        else:
            print("    Pre-downloading BirdNET model checkpoints")
            run(["uv", "run", "--no-project", "python", "-c", BIRDNET_PREDOWNLOAD], env=venv_env)
            shutil.copytree(venv_ckpt, BIRDNET_CHECKPOINT_CACHE)
            print(f"    Cached checkpoints to {BIRDNET_CHECKPOINT_CACHE}")

    print("    Running PyInstaller")
    cmd = [
        "uv", "run", "--no-project", "pyinstaller",
        "--distpath", PACKAGING_DIR / "dist",
        "--workpath", PACKAGING_DIR / "build" / appname,
        "--specpath", PACKAGING_DIR / "build" / appname,
        "--clean",
        "--noconfirm",
        "--name", appname,
        "--collect-data", "gooey",
    ]
    if is_mac:
        cmd += ["--windowed"]       # creates .app bundle, no Terminal window
    else:
        cmd += ["--onefile"]        # single .exe on Windows
    if appname == "2-analyze-pam-recordings":
        cmd += ["--collect-data", "birdnet_analyzer"]
        if sys.platform == "win32":
            # TensorFlow requires msvcp140*.dll (Visual C++ Redistributable) but
            # PyInstaller's TF hook doesn't bundle them since they live in System32,
            # not in TF's package directory. Bundle them explicitly.
            system32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
            for dll in system32.glob("msvcp140*.dll"):
                cmd += ["--add-binary", f"{dll};."]
    cmd.append(script)
    run(cmd, env=venv_env)

    print(f"    Done: {appname}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build PAM Tools distributables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "app", nargs="?", choices=APPS,
        help="App to build (default: all)",
    )
    args = parser.parse_args()

    for appname in ([args.app] if args.app else APPS):
        build(appname)

    print(f"All builds complete. Binaries are in {PACKAGING_DIR / 'dist'}/")


if __name__ == "__main__":
    main()
