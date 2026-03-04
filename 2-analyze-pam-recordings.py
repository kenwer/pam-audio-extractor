#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14" # tensorflow only has wheels for cp313
# dependencies = [
#   "birdnet-analyzer",
#   "gooey",
#   "guano",
# ]
# ///

import argparse
import csv
import math
import multiprocessing
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import guano
import birdnet_analyzer
import birdnet_analyzer.config as birdnet_cfg

USE_GUI = len(sys.argv) == 1 or "--ignore-gooey" in sys.argv
if USE_GUI:
    from gooey import Gooey, GooeyParser


def print_version_info() -> None:
    """Print version information for Python and installed packages."""
    print(f"Python executable:  {sys.executable}")
    print(f"Python version:     {sys.version}")
    print()
    print("Python packages installed in the current environment:")

    # Try uv first (for uv-managed environments), fall back to pip
    try:
        subprocess.run(["uv", "pip", "freeze"], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run([sys.executable, "-m", "pip", "freeze"], check=True)
        except subprocess.CalledProcessError:
            print("Warning: Unable to list installed packages", file=sys.stderr)


def _config_defaults(script_path: Path) -> dict:
    """Read the script's section from config.toml next to the script.

    Looks for a ``[<script-stem>]`` section, e.g. ``[2-analyze-pam-recordings]``.
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
    """Parse and return command-line arguments."""
    if USE_GUI:
        parser = GooeyParser(
            description="Batch-analyze PAM bird recordings using BirdNET-Analyzer."
        )
    else:
        parser = argparse.ArgumentParser(
            description="Batch-analyze PAM bird recordings using BirdNET-Analyzer."
        )

    def gui(**kw) -> dict:
        """Return kwargs only when running in GUI mode (Gooey widget hints)."""
        return kw if USE_GUI else {}

    # Pre-populate GUI form fields from config.toml on first launch (not on the
    # --ignore-gooey pass when the actual work runs).
    cfg = _config_defaults(Path(__file__)) if USE_GUI and "--ignore-gooey" not in sys.argv else {}

    required = parser.add_argument_group("Required")
    required.add_argument(
        "audio_dir",
        **({} if USE_GUI else {"nargs": "?"}),
        **gui(widget="DirChooser", gooey_options={"full_width": True}),
        default=cfg.get("audio_dir") or None,
        help="Path to root folder containing ARU subdirs with .wav/.WAV files",
    )

    optional = parser.add_argument_group(
        "Optional", **({} if not USE_GUI else {"gooey_options": {"columns": 3}})
    )
    optional.add_argument(
        "--min-conf",
        dest="min_conf",
        type=float,
        default=cfg.get("min_conf", 0.25),
        **gui(widget="DecimalField"),
        help="Minimum confidence threshold for detections (default: 0.25)",
    )
    optional.add_argument(
        "--top-n",
        dest="top_n",
        choices=["No limit"] + [str(i) for i in range(1, 21)],
        default="No limit" if not cfg.get("top_n") else str(cfg.get("top_n")),
        help=(
            "Maximum number of detections per 3-second segment, ranked by confidence. "
            "1 keeps only the best match per window, 2 keeps the two best, etc. "
            "No limit if omitted (default: no limit)."
        ),
    )
    optional.add_argument(
        "--output",
        default=cfg.get("output") or None,
        **gui(widget="DirChooser", gooey_options={"full_width": True}),
        help="Output directory (default: auto-generated)",
    )
    optional.add_argument(
        "--species-filter-file",
        dest="species_filter_file",
        default=cfg.get("species_filter_file") or None,
        **gui(widget="FileChooser", gooey_options={"full_width": True}),
        help="Path to species filter file",
    )
    optional.add_argument(
        "--lat",
        type=float,
        default=cfg.get("lat", -1),
        **gui(widget="DecimalField", gooey_options={"min": -90, "initial_value": cfg.get("lat", -1)}),
        help=(
            "Recording location latitude. Enables geographic (eBird-like) species "
            "filtering; requires --lon. Ignores --species-filter-file when set. "
            "Set -1 to disable (default: -1)."
        ),
    )
    optional.add_argument(
        "--lon",
        type=float,
        default=cfg.get("lon", -1),
        **gui(widget="DecimalField", gooey_options={"min": -180, "initial_value": cfg.get("lon", -1)}),
        help="Recording location longitude. See --lat. Set -1 to disable (default: -1).",
    )
    _w = cfg.get("week")
    optional.add_argument(
        "--week",
        choices=["Auto", "Year-round"] + [str(i) for i in range(1, 49)],
        default="Auto" if _w is None else ("Year-round" if _w == -1 else str(_w)),
        help=(
            "Week of year [1-48] (4 weeks per month) for seasonal species filtering; "
            "only used when --lat/--lon are set. "
            "Auto: detect from WAV GUANO metadata (most common week across all files). "
            "Year-round: disable seasonal filtering."
        ),
    )

    advanced = parser.add_argument_group("Advanced")
    advanced.add_argument(
        "--overlap",
        type=float,
        default=cfg.get("overlap", 0.0),
        **gui(widget="DecimalField"),
        help=(
            "Overlap of prediction segments in seconds [0.0, 2.9]. "
            "Higher values produce more detections but increase runtime (default: 0.0)."
        ),
    )
    advanced.add_argument(
        "--num-threads",
        dest="num_threads",
        choices=["Auto"] + [str(i) for i in [1, 2, 4, 8, 16, 32, 64, 128, 256]],
        default="Auto" if not cfg.get("num_threads") else str(cfg.get("num_threads")),
        help="Number of CPU threads for parallel file analysis (default: auto-detect via os.cpu_count())",
    )
    advanced.add_argument(
        "--version", action="store_true", help="Show version information and exit"
    )
    return parser.parse_args()


def load_species_filter(filter_path: str | Path) -> set[str]:
    """Load a species filter file into a set of label strings.

    Each non-blank, non-comment line is expected to be in BirdNET label format:
    ``Scientific name_Common name`` (e.g. ``Porzana porzana_Spotted Crake``).
    Lines starting with ``#`` and lines without an underscore (e.g. a header
    row) are skipped.

    Args:
        filter_path: Path to the species filter text file.

    Returns:
        A set of label strings to retain.
    """
    species: set[str] = set()
    with open(filter_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "_" in line:
                species.add(line)
    return species


def parse_recording_time(stem: str) -> datetime | None:
    """Parse a recording timestamp from an ARU filename stem.

    Searches for a ``YYYYMMDD_HHMMSS`` pattern anywhere in the stem to handle
    device-prefixed filenames (e.g. ``242A260564877EC4_20260225_091501``).

    Args:
        stem: The filename stem (without extension).

    Returns:
        A :class:`datetime` object, or ``None`` if no matching pattern is found.
    """
    match = re.search(r"(\d{8}_\d{6})", stem)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return None


def detect_predominant_week(audio_dir: str) -> int | None:
    """Return the most common BirdNET week number [1, 48] across all WAV files
    in *audio_dir*, or ``None`` if no GUANO timestamps could be read.

    BirdNET divides the year into 48 weeks (4 per month):
    week = (month - 1) * 4 + ceil(day / 7)
    """
    week_counts: Counter[int] = Counter()
    for wav_path in Path(audio_dir).rglob("*.WAV"):
        try:
            ts = guano.GuanoFile(str(wav_path)).get("Timestamp")
            if ts is not None:
                week_counts[min(48, (ts.month - 1) * 4 + math.ceil(ts.day / 7))] += 1
        except Exception:
            pass

    if not week_counts:
        print("No GUANO timestamps found in WAV files; using week=-1 (year-round).", file=sys.stderr)
        return None

    modal_week, modal_count = week_counts.most_common(1)[0]
    total = sum(week_counts.values())
    weeks_seen = sorted(week_counts)
    print(
        f"Auto-detected week {modal_week} ({modal_count}/{total} recordings; "
        f"weeks present: {weeks_seen[0]}–{weeks_seen[-1]})",
        file=sys.stderr,
    )
    return modal_week


def default_output_dir(min_conf: float) -> str:
    """Generate a default output directory name based on confidence and today's date.

    The format is ``birdnet_detections_all_conf_{conf}_{YYYY_MM_DD}``, where
    the decimal point in *min_conf* is replaced with an underscore.

    Args:
        min_conf: The minimum confidence threshold used for the run.

    Returns:
        A directory name such as ``birdnet-detections_conf_0_1_2026_02_26``.
    """
    date_str = datetime.now().strftime("%Y_%m_%d")
    conf_str = str(min_conf).replace(".", "_")
    return f"birdnet-detections_conf_{conf_str}_{date_str}"


def validate_audio_dir(audio_dir: str) -> None:
    """Check that audio_dir exists and contains the expected ARU subdirectory layout.

    Expected structure::

        audio_dir/
          <ARU-ID>/
            <YYYYMMDD_HHMMSS>.wav
            ...

    Hard failures (exits):
    - Path does not exist or is not a directory
    - No subdirectories found
    - No .wav files found in any subdirectory

    Warnings (printed, continues):
    - Some subdirectories contain no .wav files
    """
    path = Path(audio_dir)

    if not path.exists():
        print(f"error: audio_dir not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_dir():
        print(f"error: audio_dir is not a directory: {path}", file=sys.stderr)
        sys.exit(1)

    subdirs = sorted(d for d in path.iterdir() if d.is_dir())
    if not subdirs:
        print(f"error: audio_dir has no subdirectories — expected one per ARU device", file=sys.stderr)
        print(f"  Expected layout: {path}/<ARU-ID>/<YYYYMMDD_HHMMSS>.wav", file=sys.stderr)
        sys.exit(1)

    arus_with_audio: list[tuple[str, int]] = []
    arus_without_audio: list[str] = []
    for subdir in subdirs:
        wav_files = [f for f in subdir.iterdir() if f.is_file() and f.suffix.lower() == ".wav"]
        if wav_files:
            arus_with_audio.append((subdir.name, len(wav_files)))
        else:
            arus_without_audio.append(subdir.name)

    if not arus_with_audio:
        print(f"error: no .wav files found in any subdirectory of {path}", file=sys.stderr)
        print(f"  Expected layout: {path}/<ARU-ID>/<YYYYMMDD_HHMMSS>.wav", file=sys.stderr)
        sys.exit(1)

    if arus_without_audio:
        print(
            f"Warning: {len(arus_without_audio)} subdir(s) contain no .wav files and will be skipped: "
            + ", ".join(arus_without_audio),
            file=sys.stderr,
        )

    total_files = sum(n for _, n in arus_with_audio)
    print(f"Found {len(arus_with_audio)} ARU(s), {total_files} .wav file(s):", file=sys.stderr)
    for aru, count in arus_with_audio:
        print(f"  {aru}: {count} file(s)", file=sys.stderr)


def write_summary_tables(rows: list[dict], output_dir: str) -> None:
    """Write per-ARU and global detection summary CSVs from the filtered detections.

    Produces:
      summary-per-aru.csv  — one row per (ARU x species), sorted by ARU then count desc
      summary-all-arus.csv — one row per species across all ARUs, sorted by count desc

    Both tables include a ``max_position`` / ``best_position_any_aru`` column (e.g.
    ``top-1``, ``top-2``) indicating the best rank this species achieved within any
    single 3-second segment. Rank is determined by sorting all detections in a segment
    by confidence descending; rank 1 = highest confidence in that segment.

    Note: position is computed from the already-filtered ``rows`` (min_conf and
    species-list filters applied). Detections removed by filtering are not counted when
    ranking, so reported positions may be optimistic relative to the raw BirdNET output.
    """
    # Compute per-segment ranks
    # Group detections by (file, start_time, end_time) — one entry per 3-sec window.
    # Sort each segment by confidence descending and assign a 1-based rank.
    # For each (aru, species) pair track the best (lowest) rank seen across all segments.
    seg_groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        seg_key = (row["file"], row["start_time"], row["end_time"])
        seg_groups[seg_key].append(row)

    best_rank: dict[tuple, int] = {}  # (aru_number, species) -> best segment rank
    for seg_rows in seg_groups.values():
        seg_rows_sorted = sorted(seg_rows, key=lambda r: float(r["confidence"]), reverse=True)
        for rank, seg_row in enumerate(seg_rows_sorted, start=1):
            key = (seg_row["aru_number"], seg_row["species"])
            best_rank[key] = min(best_rank.get(key, rank), rank)

    def fmt_position(rank: int) -> str:
        return f"top-{rank}"

    # Per-ARU aggregation
    per_aru: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "max_conf": 0.0, "scientific_name": ""})
    for row in rows:
        key = (row["aru_number"], row["species"])
        per_aru[key]["count"] += 1
        per_aru[key]["max_conf"] = max(per_aru[key]["max_conf"], float(row["confidence"]))
        per_aru[key]["scientific_name"] = row["scientific_name"]

    per_aru_path = str(Path(output_dir) / "summary-per-aru.csv")
    with open(per_aru_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["aru_number", "species", "scientific_name", "detection_count", "max_confidence", "max_position"]
        )
        writer.writeheader()
        for (aru, species), data in sorted(per_aru.items(), key=lambda x: (x[0][0], -x[1]["count"])):
            writer.writerow({
                "aru_number": aru,
                "species": species,
                "scientific_name": data["scientific_name"],
                "detection_count": data["count"],
                "max_confidence": f"{data['max_conf']:.4f}",
                "max_position": fmt_position(best_rank[(aru, species)]),
            })

    # Global aggregation
    global_agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "max_conf": 0.0, "arus": set(), "scientific_name": "", "best_rank": float("inf")})
    for (aru, species), data in per_aru.items():
        global_agg[species]["count"] += data["count"]
        global_agg[species]["max_conf"] = max(global_agg[species]["max_conf"], data["max_conf"])
        global_agg[species]["arus"].add(aru)
        global_agg[species]["scientific_name"] = data["scientific_name"]
        global_agg[species]["best_rank"] = min(global_agg[species]["best_rank"], best_rank[(aru, species)])

    global_path = str(Path(output_dir) / "summary-all-arus.csv")
    with open(global_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["species", "scientific_name", "total_detections", "max_confidence", "aru_count", "best_position_any_aru"]
        )
        writer.writeheader()
        for species, data in sorted(global_agg.items(), key=lambda x: -x[1]["count"]):
            writer.writerow({
                "species": species,
                "scientific_name": data["scientific_name"],
                "total_detections": data["count"],
                "max_confidence": f"{data['max_conf']:.4f}",
                "aru_count": len(data["arus"]),
                "best_position_any_aru": fmt_position(int(data["best_rank"])),
            })

    print(f"  Per-ARU summary : {per_aru_path}", file=sys.stderr)
    print(f"  Global summary  : {global_path}", file=sys.stderr)


def main() -> None:
    """Entry point: run BirdNET-Analyzer and write an enriched detections CSV."""
    args = parse_args()

    if args.version:
        print_version_info()
        return

    if not args.audio_dir:
        print("error: audio_dir is required", file=sys.stderr)
        sys.exit(1)

    validate_audio_dir(args.audio_dir)

    output_dir = args.output or default_output_dir(args.min_conf)
    csv_output_path = str(Path(output_dir) / "All-BirdNET-detections.csv")

    species_set: set[str] | None = None
    if args.species_filter_file:
        if args.lat != -1 and args.lon != -1:
            print(
                "Warning: --species-filter-file is ignored when --lat/--lon are set. "
                "BirdNET uses geographic (eBird-like) occurrence probabilities instead.",
                file=sys.stderr,
            )
        else:
            species_set = load_species_filter(args.species_filter_file)
            print(f"Loaded {len(species_set)} species from filter file.", file=sys.stderr)

    # week=None (omitted by user): auto-detect from GUANO when lat/lon are set
    # week=-1   (explicitly set):  year-round (passed as-is to BirdNET)
    # week=1-48 (explicitly set):  used directly
    if args.week in (None, "Auto"):
        week = None
    elif args.week == "Year-round":
        week = -1
    else:
        week = int(args.week)
    if week is None and args.lat != -1 and args.lon != -1:
        week = detect_predominant_week(args.audio_dir)  # if None, BirdNET uses -1

    top_n = None if args.top_n in (None, "No limit") else int(args.top_n)
    num_threads = os.cpu_count() or 8 if args.num_threads in (None, "Auto") else int(args.num_threads)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"Running BirdNET-Analyzer on {args.audio_dir} ...", file=sys.stderr)

    # Runs BirdNET on every audio file found recursively under audio_dir.
    # Per-file results (*.BirdNET.results.csv) are written to output_dir,
    # mirroring the subdirectory structure of audio_dir.
    # slist: pre-filters detections to the species in the filter file during
    # analysis (hopefully more efficient than post-filtering). Has no effect when lat/lon
    # are provided — in that case BirdNET uses the geographic species model instead.
    # API source: https://github.com/birdnet-team/BirdNET-Analyzer/blob/main/birdnet_analyzer/analyze/core.py
    birdnet_analyzer.analyze(
        args.audio_dir,
        output=output_dir,
        min_conf=args.min_conf,
        slist=args.species_filter_file,
        lat=args.lat,
        lon=args.lon,
        week=week,
        overlap=args.overlap,
        top_n=top_n,
        rtype="csv",
        combine_results=False,
        threads=num_threads,
        #show_progress=True,  # suppresses per-file output; uncomment once available https://github.com/birdnet-team/BirdNET-Analyzer/pull/854
        #batch_size = 16,
        locale="en",
        #use_perch = True,
    )

    result_csvs = sorted(Path(output_dir).rglob("*.BirdNET.results.csv"))

    if not result_csvs:
        print("No detections found.", file=sys.stderr)
        return

    # Run-level context columns sourced directly from args/birdnet_cfg.
    # week is -1 when year-round (BirdNET sentinel); None means auto-detect failed.
    run_context = {
        "lat": args.lat,
        "lon": args.lon,
        "week": week if week is not None else -1,
        "species_list": args.species_filter_file or "",
        "min_conf": args.min_conf,
        "model": os.path.basename(birdnet_cfg.MODEL_PATH),
    }

    fieldnames = [
        "file",
        "aru_number",
        "species",
        "scientific_name",
        "confidence",
        "start_time",
        "end_time",
        "recording_time",
        *run_context.keys(),
    ]

    detections: list[dict] = []

    with open(csv_output_path, "w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        for result_csv in result_csvs:
            with open(result_csv, newline="", encoding="utf-8") as infile:
                for row in csv.DictReader(infile):
                    scientific_name: str = row["Scientific name"]
                    label: str = f"{scientific_name}_{row['Common name']}"

                    if species_set is not None and label not in species_set:
                        continue
                    if float(row["Confidence"]) < args.min_conf:
                        continue

                    file_path = Path(row["File"])
                    aru_number: str = file_path.parent.name
                    recording_time: datetime | None = parse_recording_time(file_path.stem)

                    detection = {
                        "file": row["File"],
                        "aru_number": aru_number,
                        "species": row["Common name"],
                        "scientific_name": scientific_name,
                        "confidence": row["Confidence"],
                        "start_time": row["Start (s)"],
                        "end_time": row["End (s)"],
                        "recording_time": str(recording_time) if recording_time else "",
                        **run_context,
                    }
                    writer.writerow(detection)
                    detections.append(detection)

    print(file=sys.stderr)
    print(f"Total detections: {len(detections)}", file=sys.stderr)
    print(f"  Output dir    : {output_dir}/", file=sys.stderr)
    print(f"  Detections CSV: {csv_output_path}", file=sys.stderr)
    write_summary_tables(detections, output_dir)


if USE_GUI:
    main = Gooey(
        program_name="Analyze PAM Recordings",
        show_progress_bar=True,
        default_size=(800, 700),
        navigation="TABBED",
        show_stop_button=False,
        body_width=80,
    )(main)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
