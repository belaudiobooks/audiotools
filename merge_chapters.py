"""Merge split audio files into chapters based on user input."""

import logging
import os
import tempfile
import time
from collections.abc import Sequence

import ffmpeg
import vlc

from util import get_duration_sec


def play_first_n_seconds(file: str, seconds: float):
    """Play first N seconds of an audio file."""
    player = vlc.MediaPlayer(file)
    player.play()
    time.sleep(seconds)
    player.stop()


def ask_new_chapter(filename: str) -> bool:
    """Ask user if this file starts a new chapter. Returns True if new chapter."""
    while True:
        response = input(f"Is '{filename}' a NEW chapter? [y/n]: ").strip().lower()
        if response in ("y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        print("Please enter 'y' for new chapter or 'n' for continuation.")


def merge_files(files: Sequence[str], output_file: str):
    """Merge multiple audio files into one using ffmpeg concat."""
    if len(files) == 1:
        # Just copy if single file
        ffmpeg.input(files[0]).output(output_file, acodec="copy").run(
            overwrite_output=True, capture_stdout=True, capture_stderr=True
        )
        return

    # Use concat demuxer for lossless concatenation
    # Create a temporary file list for ffmpeg concat demuxer
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for file in files:
            # Escape single quotes in filenames
            escaped = file.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        concat_list = f.name

    try:
        ffmpeg.input(concat_list, format="concat", safe=0).output(
            output_file, acodec="copy"
        ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
    finally:
        os.unlink(concat_list)


def merge_chapters(
    files: Sequence[str],
    out_dir: str,
    play_seconds: float = 5.0,
):
    """
    Iterate through audio files, play beginning of each, and ask user
    if it's a new chapter or continuation. Merge files accordingly.
    """
    if not files:
        logging.error("No files provided.")
        return

    files = sorted(files)

    # Calculate total duration before merging
    logging.info("Calculating total duration of input files...")
    total_input_duration = sum(get_duration_sec(f) for f in files)
    logging.info(f"Total input duration: {total_input_duration:.2f} seconds")

    # Group files into chapters
    chapters: list[list[str]] = []
    current_chapter: list[str] = []

    for i, file in enumerate(files):
        filename = os.path.basename(file)
        print(f"\n--- File {i + 1}/{len(files)}: {filename} ---")
        print(f"Playing first {play_seconds} seconds...")
        play_first_n_seconds(file, play_seconds)

        if i == 0:
            # First file always starts a new chapter
            print("(First file - automatically starting new chapter)")
            current_chapter = [file]
        else:
            is_new = ask_new_chapter(filename)
            if is_new:
                # Save previous chapter and start new one
                chapters.append(current_chapter)
                current_chapter = [file]
                print(f"Starting chapter {len(chapters) + 1}")
            else:
                current_chapter.append(file)
                print(f"Adding to chapter {len(chapters) + 1}")

    # Don't forget the last chapter
    if current_chapter:
        chapters.append(current_chapter)

    # Report chapter structure
    print("\n=== Chapter structure ===")
    for i, chapter_files in enumerate(chapters):
        files_str = ", ".join(os.path.basename(f) for f in chapter_files)
        print(f"Chapter {i + 1}: {files_str}")

    # Merge chapters
    print(f"\n=== Merging {len(chapters)} chapters ===")
    output_files = []
    for i, chapter_files in enumerate(chapters):
        # Use zero-padded numbering based on total chapter count
        padding = len(str(len(chapters)))
        output_name = f"{str(i + 1).zfill(padding)}.mp3"
        output_path = os.path.join(out_dir, output_name)
        output_files.append(output_path)

        files_str = ", ".join(os.path.basename(f) for f in chapter_files)
        logging.info(f"Merging chapter {i + 1}: {files_str} -> {output_name}")
        merge_files(chapter_files, output_path)

    # Verify total duration
    logging.info("Verifying output duration...")
    total_output_duration = sum(get_duration_sec(f) for f in output_files)
    logging.info(f"Total output duration: {total_output_duration:.2f} seconds")

    diff = abs(total_input_duration - total_output_duration)
    if diff > 1.0:
        logging.warning(
            f"Duration mismatch! Input: {total_input_duration:.2f}s, "
            f"Output: {total_output_duration:.2f}s, Diff: {diff:.2f}s"
        )
    else:
        logging.info(f"Duration verified (diff: {diff:.2f}s)")

    print(f"\n=== Done! Created {len(chapters)} chapter files in {out_dir} ===")
