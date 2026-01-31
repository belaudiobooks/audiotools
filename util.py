"""Common utility functions for audio processing."""

import functools
import os

import ffmpeg

DEFAULT_BITRATE = 224000


@functools.cache
def get_duration_sec(file: str) -> float:
    """Get duration of audio file in seconds."""
    result = ffmpeg.probe(file)
    return float(result["format"]["duration"])


@functools.cache
def get_bitrate(file: str) -> int:
    """Get bitrate of audio file."""
    return int(ffmpeg.probe(file)["format"]["bit_rate"])


@functools.cache
def get_file_size(file: str) -> int:
    """Get file size in bytes."""
    return os.stat(file).st_size
