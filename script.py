import argparse
from collections.abc import Sequence, MutableMapping, MutableSequence
import logging
import os
import re
from dataclasses import dataclass
import subprocess
import tempfile
import threading
import time


import ffmpeg
import vlc


logging.basicConfig(
    format="%(asctime)s,%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)

current_progress: MutableMapping[str, int] = {}
progress_lock = threading.Lock()


@dataclass
class Silence:
    start_sec: float
    end_sec: float
    duration_sec: float


def get_silences(file: str) -> Sequence[Silence]:
    command = (
        ffmpeg.input(file)
        .audio.filter("silencedetect", n="-50dB", d="0.5")
        .output("pipe:", format="null")
    )
    _, err = ffmpeg.run(command, capture_stderr=True)
    result: Sequence[Silence] = []
    for end, duration in re.findall(
        "silence_end: ([\\.\\d]+) \\| silence_duration: ([\\.\\d]+)", err.decode("utf8")
    ):
        result.append(
            Silence(
                start_sec=float(end) - float(duration),
                end_sec=float(end),
                duration_sec=float(duration),
            )
        )
    return result


def generate_silence(duration_sec: float) -> ffmpeg.Stream:
    return ffmpeg.input(f"anullsrc=duration={duration_sec}", f="lavfi")


def last_few_sec_with_silence_and_beep(
    file: str, silence_to_add_sec: float = 0, few_sec: float = 1
) -> str:
    _, res = tempfile.mkstemp(prefix="audiotools", suffix=".mp3")
    beep = ffmpeg.input("sine=d=0.5:f=800", f="lavfi")
    dur_sec = get_duration_sec(file) - (2 - silence_to_add_sec + few_sec)
    ffmpeg.concat(
        ffmpeg.input(file, ss=dur_sec),
        generate_silence(silence_to_add_sec),
        beep,
        a=1,
        v=0,
    ).output(res, audio_bitrate=get_bitrate(file), ar=44100).run(
        overwrite_output=True, capture_stdout=True, capture_stderr=True
    )
    return res


def pad_with_silence(file: str, silence_sec: float, dir: str) -> subprocess.Popen:
    logging.info(f"Padding {os.path.basename(file)} with {silence_sec}s silence")
    return (
        ffmpeg.concat(
            ffmpeg.input(file), generate_silence_stream(silence_sec), a=1, v=0
        )
        .output(
            os.path.join(dir, os.path.basename(file)),
            audio_bitrate=get_bitrate(file),
            ar=44100,
        )
        .run_async(overwrite_output=True, quiet=True)
    )


def wait_till_finished(process: subprocess.Popen, file: str):
    assert process.stderr
    assert process.stdout
    buffer = ""
    expected_duration = get_duration_sec(file)
    short_name = os.path.basename(file)
    while True:
        out = process.stderr.read(10)
        buffer += out.decode("utf8", errors="ignore")
        # ffmpeg prints progess by printing time=hh:mm:ss text.
        # We use it to understand how much is left related to the 
        # duration of the original file.
        matches = re.findall("time=(\\d+):(\\d+):(\\d+)", buffer)
        if len(matches) > 0:
            buffer = ""
            hour, min, sec = matches[0]
            progress = int(
                100 * (int(hour) * 3600 + int(min) * 60 + int(sec)) / expected_duration
            )
            progress_lock.acquire()
            current_progress[short_name] = progress
            progress_lock.release()
        if process.poll() == 0:
            break
    progress_lock.acquire()
    current_progress[short_name] = 100
    progress_lock.release()


def play_file(file: str):
    player = vlc.MediaPlayer(file)
    player.play()
    time.sleep(get_duration_sec(file))


def get_duration_sec(file: str) -> float:
    result = ffmpeg.probe(file)
    return float(result["format"]["duration"])


def get_bitrate(file: str) -> int:
    return int(ffmpeg.probe(file)["format"]["bit_rate"])


def how_much_silence_to_add_sec(file: str, min_silence_sec: float) -> float:
    silences = get_silences(file)

    if len(silences) == 0:
        # there are no silences at all in the file
        return min_silence_sec

    last_silence = silences[-1]
    duration = get_duration_sec(file)

    # the last silence is not at the end of the file
    if duration - last_silence.end_sec > 0.2:
        return min_silence_sec

    return max(0, min_silence_sec - last_silence.duration_sec)


def maybe_pad_file_with_silence(
    file: str, out_directory: str, min_silence_sec=2
) -> threading.Thread | None:
    """
    Given a file add silence to the end if it doesn't have enough.
    Returns thread that watches the process that updates the file.
    """
    short_name = os.path.basename(file)
    to_add_sec = how_much_silence_to_add_sec(file, min_silence_sec)
    if to_add_sec == 0:
        logging.info(f"To file {short_name} no need to add silence")
        return None
    logging.info(f"Adding {to_add_sec}s silence to file {short_name}")
    preview = last_few_sec_with_silence_and_beep(file, silence_to_add_sec=to_add_sec)
    play_file(preview)
    process = pad_with_silence(file, silence_sec=to_add_sec, dir=out_directory)
    thread = threading.Thread(
        name=f"padding-{short_name}", target=wait_till_finished, args=(process, file)
    )
    thread.start()
    return thread


def print_progress():
    progress_lock.acquire()
    progress_str = [
        f"{file}: {progress}%" for file, progress in current_progress.items()
    ]
    for file in list(current_progress.keys()):
        if current_progress[file] == 100:
            current_progress.pop(file)
    progress_lock.release()
    logging.info("Progress: " + "\t".join(progress_str))


def pad_silence_to_files(files: Sequence[str], out_dir: str):
    """Takes a list of files and adds silence to the end if there is not enough
    silence."""
    threads: MutableSequence[threading.Thread | None] = []
    audiofiles = sorted(files, key=get_duration_sec, reverse=True)
    for file in audiofiles:
        threads.append(maybe_pad_file_with_silence(file, out_directory=out_dir))
        print_progress()
    while True:
        some_thread_alive = any(
            [thread is not None and thread.is_alive() for thread in threads]
        )
        if some_thread_alive:
            print_progress()
            time.sleep(2)
        else:
            break


def main():
    parser = argparse.ArgumentParser(description="Audiobook tools.")
    parser.add_argument("--operation", choices=["pad_silence"], required=True)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("audiofiles", nargs="+", metavar="001.mp3")

    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    if args.operation == "pad_silence":
        pad_silence_to_files(args.audiofiles, args.out_dir)


if __name__ == "__main__":
    main()
