# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Audiotools is a Python CLI for processing Belarusian audiobooks. It handles audio manipulation (padding, cutting, quality normalization), podcast generation with RSS feeds, and YouTube video creation.

## Commands

```bash
# Setup
python -m venv ./venv
source ./venv/bin/activate
pip install -r requirements.txt
pre-commit install

# Run CLI
python script.py --help
python script.py --operation pad_silence --out_dir ./output *.mp3
python script.py --operation cut --out_dir ./output *.mp3
python script.py --operation ensure_quality --out_dir ./output *.mp3
python script.py --operation create_podcast --out_dir ./output /path/to/book
python script.py --operation create_youtube --out_dir ./output /path/to/book
python script.py --operation merge_chapters --out_dir ./output --play_seconds 10 *.mp3

# Code quality (runs automatically via pre-commit)
black .
flake8
```

## Architecture

**Entry point:** `script.py` - argparse-based CLI with 6 operations

**Modules:**
- `util.py` - Common utilities: `get_duration_sec()`, `get_bitrate()`, `get_file_size()`, `DEFAULT_BITRATE`
- `scheduler.py` - Thread-pool executor for parallel ffmpeg operations (CPU_count - 1 threads). Parses ffmpeg stderr for progress tracking. Processes files largest-first for load balancing.
- `book.py` - Dataclass for book metadata. Validates directory structure (description.txt, ex-1.mp3, ex-2.mp3, cover images). Parses Belarusian metadata.
- `podcast.py` - Generates podcast files and RSS feeds. Concatenates intro/outro, resizes covers, calculates GCS paths.
- `youtube.py` - Generates YouTube videos via external `books-video-generator` tool. Creates chapters.csv and video descriptions.
- `merge_chapters.py` - Interactive tool for merging split audio files into chapters. Plays file beginnings, asks user for chapter boundaries.

**External dependencies:** FFmpeg (audio processing), ImageMagick (image resizing), VLC (interactive cutting)

## Book Directory Structure

Expected structure for `create_podcast` and `create_youtube` operations:
```
book_folder/
├── description.txt    # Belarusian metadata
├── ex-1.mp3          # Intro excerpt
├── ex-2.mp3          # Outro excerpt
├── art.jpg           # Cover image
├── youtube.jpg       # YouTube thumbnail (optional)
└── *.mp3             # Numbered chapter files
```
