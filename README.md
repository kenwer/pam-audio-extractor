# PAM Audio Extractor
A set of Python scripts to analyze Passive Acoustic Monitoring (PAM) bird recordings using [BirdNET-Analyzer](https://github.com/BirdNET-Team/BirdNET-Analyzer), and to extract the highest-confidence detections as audio snippets for human review.

## Prerequisites
This project requires [`uv`](https://docs.astral.sh/uv/) that handles Python plus all library installs, and [`ffmpeg`](https://ffmpeg.org/) which is required by BirdNET for audio decoding.

Run the installer script to install both and pre-populate the uv cache:
* using the installer script:
  ```shell
  ./0-install-prereqs.sh # Linux and macOS
  0-install-prereqs.bat  # Windows
  ```
* or install manually:
  ```shell
  brew install uv ffmpeg         # macOS
  winget install uv ffmpeg       # Windows 
  sudo apt-get install uv ffmpeg # Debian/Ubuntu based Linux
  sudo dnf install uv ffmpe      # Fedora/RHEL based Linux
  sudo pacman -S uv ffmpeg       # Arch/Manjaro based Linux
  ```

> Note: on the first run `uv` downloads Python and all dependencies automatically. You may run any script once with `--version` beforehand to populate the cache and avoid delays at the start of a session:
> ```bash
> ./1-import-pam-recordings.py --version
> ./2-analyze-pam-recordings.py --version
> ./3-extract-top-detections.py --version
> ```


## Workflow
The tooling covers three steps: 1) copy recordings from SD cards onto disk, 2) run BirdNET to detect species across all recordings, then 3) extract the highest-confidence snippets per species for human review.

Each step can be performed using the **GUI** or using the **CLI**. The GUI opens when the script is run with no arguments. If a `config.toml` exists, the form will use it to pre-populate parameters. On Windows, use the `.bat` launcher instead of the `.py` file.


### Step 1 — Import SD card recordings

* GUI:
  ```shell
  ./1-import-pam-recordings.py  # Linux and macOS
  1-import-pam-recordings.bat   # Windows
  ```
* CLI:
  ```shell
  ./1-import-pam-recordings.py /path/to/audio-recordings
  ```

Once running, it waits for SD cards to be inserted into the reader. The script detects each matching volume, copies its WAV files into `<target_dir>/<card-name>/`, and ejects the card when finished.

| Option | Default | Description |
|---|---|---|
| `target_dir` | — | Root folder where `MSD-*/` subdirectories are created |
| `--card-pattern` | `^MSD-` | Regex matched against the SD card volume name (case-insensitive) |
| `--overwrite` | off | Overwrite files that already exist in the destination |
| `--num-workers` | `2` | Number of cards to copy concurrently |

---

### Step 2 — Analyze recordings with BirdNET

* GUI:
  ```shell
  ./2-analyze-pam-recordings.py  # Linux and macOS
  2-analyze-pam-recordings.bat   # Windows
  ```
* CLI:
  ```shell
  # with a manual species list
  ./2-analyze-pam-recordings.py /path/to/audio-recordings --species-filter-file ./custom_species_list.txt

  # with geographic filtering (eBird-based occurrence model, week auto-detected from WAV metadata)
  ./2-analyze-pam-recordings.py /path/to/audio-recordings --lat 48.52 --lon 9.05

  # with geographic filtering, explicit week, and overlapping windows
  ./2-analyze-pam-recordings.py /path/to/audio-recordings --lat 48.52 --lon 9.05 --week 14 --overlap 1.5

  # keep only the single best detection per 3-second window (suppresses spurious secondary matches)
  ./2-analyze-pam-recordings.py /path/to/audio-recordings --lat 48.52 --lon 9.05 --top-n 1
  ```

Output is written to the specified output directory:
```
birdnet-detections_conf_0_25_2026_02_26/
  All-BirdNET-detections.csv          # enriched detections for all ARUs
  summary-per-aru.csv                 # detection count and max confidence per (ARU × species)
  summary-all-arus.csv                # detection count and max confidence per species across all ARUs
  BirdNET_CombinedTable.csv           # raw BirdNET output
  MSD-109/
    20260225_064500.BirdNET.results.csv
    ...
```

| Option | Default | Description |
|---|---|---|
| `audio_dir` | — | Root folder containing ARU subdirectories |
| `--species-filter-file` | none | Species filter file (`Scientific name_Common name`, one per line). Ignored when `--lat`/`--lon` are set. |
| `--min-conf` | `0.25` | Minimum confidence threshold (0–1) |
| `--top-n` | no limit | Maximum detections per 3-second segment [1–20], ranked by confidence. Useful to suppress low-ranked secondary matches that are usually incorrect. |
| `--output` | auto-generated | Override output directory |
| `--lat` | `-1` (off) | Recording location latitude. Enables geographic (eBird-like) species filtering; requires `--lon`. |
| `--lon` | `-1` (off) | Recording location longitude. See `--lat`. |
| `--week` | `Auto` | Week of year [1–48] for seasonal filtering. Only used with `--lat`/`--lon`. `Auto`: detect from WAV GUANO metadata. `Year-round`: disable seasonal filtering. |
| `--overlap` | `0.0` | Overlap of prediction segments in seconds [0.0–2.9]. Higher values produce more detections at the cost of longer runtime. |
| `--num-threads` | auto | Number of CPU threads for parallel file analysis. Auto-detected via `os.cpu_count()`. |

#### Species filtering
The script allows to filter species according to this decision tree:
```
Are --lat and --lon provided?
├─ YES: use geographic model (ignores --species-filter-file)
│       is --week provided?
│        ├─ YES: use specified week (or Year-round for year-round)
│        └─ NO:  auto-detect from WAV GUANO metadata (most common week)
└─ NO:  is --species-filter-file provided?
         ├─ YES: restrict analysis to the listed species
         └─ NO:  analyse all ~6,000 species in the BirdNET model
```

**Geographic model:** BirdNET bundles a TFLite model (checkpoints/V2.4/BirdNET_GLOBAL_6K_V2.4_MData_Model_V2_FP16.tflite) trained on eBird occurrence data that takes lat/lon/week as input and returns a per-species occurrence probability. Only species with a probability above a threshold (default 0.03) are included in the candidate species list that BirdNET searches for. It produces the same kind of location- and season-aware species list that tools like Chirpity offer via live eBird queries. See the [BirdNET-Analyzer discussion on species filtering](https://github.com/birdnet-team/BirdNET-Analyzer/discussions/234) for more detail.

When `--lat`/`--lon` are set and `--week` is omitted, the week is auto-detected from the WAV GUANO metadata (most common week across all recordings).

**Species filter file format** - one BirdNET label per line:
```
# Target species
Turdus merula_Eurasian Blackbird
Porzana porzana_Spotted Crake
Ardea cinerea_Grey Heron
```
---

### Step 3 — Extract top detections for review

* GUI:
  ```shell
  ./3-extract-top-detections.py  # Linux and macOS
  3-extract-top-detections.bat   # Windows
  ```
* CLI:
  ```shell
  ./3-extract-top-detections.py birdnet-detections_conf_0_25_2026_02_26/All-BirdNET-detections.csv
  ```

Snippets are written to the specified output directory. Each filename encodes ARU, species, rank, confidence, timestamp, and detection window:
```
MSD-109_-_Turdus merula_Eurasian Blackbird_-_01_conf0.8014_20260225_064500_6.0-9.0.wav
```

| Option | Default | Description |
|---|---|---|
| `detections_csv` | — | Path to `All-BirdNET-detections.csv` |
| `--top-n` | `10` | Max snippets per (ARU, species) pair, ranked by confidence |
| `--padding` | `3.0` | Seconds of audio before/after each detection window |
| `--output` | `<csv_dir>/top-detections` | Override output directory |
| `--species-filter-file` | none | Species filter file |
| `--aru` | all | Restrict to specific ARUs (repeatable) |
| `--date-from` / `--date-to` | none | Date range filter (`YYYY-MM-DD`) |

```bash
# One ARU, filtered to species list, from a specific date
./3-extract-top-detections.py All-BirdNET-detections.csv \
  --aru MSD-109 --species-filter-file ./custom_species_list.txt --date-from 2026-02-25

# Top 5 snippets with tighter padding
./3-extract-top-detections.py All-BirdNET-detections.csv --top-n 5 --padding 1.5
```

---

## Audio File Structure

`1-import-pam-recordings.py` creates this layout automatically. If importing manually, match it exactly:
```
audio-recordings/
  MSD-109/
    20260225_064500.WAV
    20260225_070000.WAV
    ...
  MSD-110/
    20260225_064500.WAV
    ...
```
- Each subdirectory name is used as the ARU identifier
- Filenames must follow `YYYYMMDD_HHMMSS` for timestamps to be parsed
- Files should be 16-bit PCM WAV, mono, 48 000 Hz (standard ARU output)

## License

This project is licensed under the AGPL-3.0 license. See the LICENSE file for the full text.