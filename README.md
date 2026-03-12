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


### Step 1 - Import SD card recordings

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
| `target_dir` | - | Root folder where `MSD-*/` subdirectories are created |
| `--card-pattern` | `^MSD-` | Regex matched against the SD card volume name (case-insensitive) |
| `--overwrite` | off | Overwrite files that already exist in the destination |
| `--num-workers` | `2` | Number of cards to copy concurrently |


#### Resulting audio file structure
The `1-import-pam-recordings.py` creates the following layout:
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
- Filenames follow `YYYYMMDD_HHMMSS` for timestamps to be parsed
- Files should be 16-bit PCM WAV, mono, 48 000 Hz (standard ARU output)

---

### Step 2 - Analyze recordings with BirdNET

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

| Option | Default | Description |
|---|---|---|
| `audio_dir` | - | Root folder containing ARU subdirectories |
| `--species-filter-file` | none | Species filter file (`Scientific name_Common name`, one per line). Ignored when `--lat`/`--lon` are set. |
| `--min-conf` | `0.25` | Minimum confidence threshold (0–1) |
| `--top-n` | no limit | Maximum detections per 3-second segment [1–20], ranked by confidence. Useful to suppress low-ranked secondary matches that are usually incorrect. |
| `--output` | auto-generated | Override output directory |
| `--lat` | `-1` (off) | Recording location latitude. Enables geographic (eBird-like) species filtering; requires `--lon`. |
| `--lon` | `-1` (off) | Recording location longitude. See `--lat`. |
| `--week` | `Auto` | Week of year [1–48] for seasonal filtering. Only used with `--lat`/`--lon`. `Auto`: detect from WAV GUANO metadata. `Year-round`: disable seasonal filtering. |
| `--locale` | none | Add localized species name columns to the output CSVs (e.g. `de`, `fr`). One per line in GUI. Available codes: `af`, `ar`, `bg`, `ca`, `cs`, `da`, `de`, `el`, `en_uk`, `es`, `fi`, `fr`, `he`, `hr`, `hu`, `in`, `is`, `it`, `ja`, `ko`, `lt`, `ml`, `nl`, `no`, `pl`, `pt_BR`, `pt_PT`, `ro`, `ru`, `sk`, `sl`, `sr`, `sv`, `th`, `tr`, `uk`, `zh`. |
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

#### Output structure
The output is written to the specified output directory, for example:
```
birdnet-detections_conf25_2026-02-26/
  all-birdnet-detections.csv   # one row per detection, all ARUs combined
  summary-per-aru.csv          # one row per (ARU × species)
  summary-all-arus.csv         # one row per species across all ARUs
  species-list.txt             # geographic species list (only when --lat/--lon are set)
  MSD-109/
    20260225_064500.BirdNET.results.csv
    ...
```

**all-birdnet-detections.csv** contains one row per detection above `--min-conf`:
| Column | Description |
|---|---|
| `file` | Absolute path to the source WAV file |
| `aru_number` | ARU identifier (name of the subdirectory containing the file) |
| `scientific_name` | Scientific name as reported by BirdNET |
| `species` | Common name as reported by BirdNET (English) |
| `species_{locale}` | Localized common name for each `--locale` requested (e.g. `species_de`). Column only present when the corresponding locale was specified. |
| `confidence` | BirdNET confidence score (0–1) |
| `segment_rank` | Rank of this species within its 3-second window, sorted by confidence descending (1 = highest-confidence detection in that window, 2 = second-highest, etc.). Useful for spotting likely false positives: a species consistently ranked 2 or lower was always outcompeted by another species in the same window. |
| `start_time` | Start of the 3-second detection window in seconds |
| `end_time` | End of the 3-second detection window in seconds |
| `recording_time` | Recording start timestamp parsed from the filename (`YYYY-MM-DD HH:MM:SS`) |
| `lat` | Latitude used for analysis (`-1` if not set) |
| `lon` | Longitude used for analysis (`-1` if not set) |
| `week` | BirdNET week number used for seasonal filtering (1–48; `-1` = year-round) |
| `species_list` | Path to the species filter file used (empty if not set) |
| `min_conf` | Minimum confidence threshold applied |
| `model` | BirdNET model filename used |

**summary-all-arus.csv** contains one row per species across all ARUs, sorted by total detection count descending:
| Column | Description |
|---|---|
| `scientific_name` | Scientific name |
| `species` | Common name (English) |
| `species_{locale}` | Localized common name for each `--locale` requested. Column only present when the corresponding locale was specified. |
| `total_detections` | Total detection count across all ARUs |
| `max_confidence` | Highest confidence score seen across all ARUs |
| `aru_count` | Number of distinct ARUs where this species was detected |
| `best_segment_rank_any_aru` | Best (lowest) `segment_rank` this species achieved in any single 3-second window across all ARUs |

**summary-per-aru.csv** contains one row per (ARU × species), sorted by ARU then detection count descending:
| Column | Description |
|---|---|
| `aru_number` | ARU identifier |
| `scientific_name` | Scientific name |
| `species` | Common name (English) |
| `species_{locale}` | Localized common name for each `--locale` requested. Column only present when the corresponding locale was specified. |
| `detection_count` | Number of detections above `--min-conf` |
| `max_confidence` | Highest confidence score seen across all detections |
| `best_segment_rank` | Best (lowest) `segment_rank` this species achieved in any single 3-second window at this ARU (e.g. `top-1` means it was the dominant detection in at least one window) |

**species-list.txt** contains the species list that the geographic model returned for the lat/lon/week. Only written when `--lat`/`--lon` are set. It represents the candidate species list BirdNET used for this run.

---

### Step 3 - Extract top detections for review

* GUI:
  ```shell
  ./3-extract-top-detections.py  # Linux and macOS
  3-extract-top-detections.bat   # Windows
  ```
* CLI:
  ```shell
  ./3-extract-top-detections.py birdnet-detections_conf25_2026-02-26/all-birdnet-detections.csv
  ```

Snippets are written to `top-detection-snippets/` next to the source audio recordings. Each filename encodes ARU, species, segment rank (rank of this species within its 3-second window - 1 = highest-confidence detection in that window), confidence, timestamp, and detection window:
```
MSD-109_-_Turdus iliacus_Redwing_-_segrank2_-_conf0.4691_-_20260225_064500_-_36.0_-_39.0.wav
```

| Option | Default | Description |
|---|---|---|
| `detections_csv` | - | Path to `all-birdnet-detections.csv` |
| `--top-n` | `10` | Max snippets per (ARU, species) pair, ranked by confidence |
| `--padding` | `1.5` | Seconds of audio before/after each detection window |
| `--output` | `<audio_dir>/top-detection-snippets` | Override output directory |
| `--species-filter-file` | none | Species filter file |
| `--aru` | all | Restrict to specific ARUs (one per line in GUI) |
| `--date-from` / `--date-to` | none | Date range filter (`YYYY-MM-DD`) |

```bash
# One ARU, filtered to species list, from a specific date
./3-extract-top-detections.py all-birdnet-detections.csv \
  --aru MSD-109 --species-filter-file ./custom_species_list.txt --date-from 2026-02-25

# Top 5 snippets with tighter padding
./3-extract-top-detections.py all-birdnet-detections.csv --top-n 5 --padding 1.5
```

---

## License

This project is licensed under the AGPL-3.0 license. See the LICENSE file for the full text.