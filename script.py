import argparse
from collections.abc import Sequence
import functools
import logging
import os
import re
from dataclasses import dataclass
import tempfile
import time
from typing import Any
import readchar
import shutil


import ffmpeg
import vlc

from podcast import create_podcast
from scheduler import Scheduler
from youtube import YoutubeVideoType, create_youtube

DEFAULT_BITRATE = 224000


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected.")


logging.basicConfig(
    format="%(asctime)s,%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)


@dataclass
class Silence:
    start_sec: float
    end_sec: float
    duration_sec: float


def create_tmp_audiofile() -> str:
    return tempfile.mkstemp(prefix="audiotools", suffix=".mp3")[1]


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
    # Generate silence into a separate file and return it as a stream.
    # Originally we returned stream immediately without saving it into a file.
    # It lead to noise being added to the main file. Don't know the root cause of the
    # noise, but getting rid of "on-fly" streams fixes it.
    silence_file = create_tmp_audiofile()
    ffmpeg.input(f"anullsrc=duration={duration_sec}", f="lavfi").output(
        silence_file, audio_bitrate=DEFAULT_BITRATE, ar=44100
    ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
    return ffmpeg.input(silence_file)


def last_few_sec_with_silence_and_beep(
    file: str, silence_to_add_sec: float = 0, few_sec: float = 1
) -> str:
    beep = ffmpeg.input("sine=d=0.5:f=800", f="lavfi")
    dur_sec = get_duration_sec(file) - (2 - silence_to_add_sec + few_sec)
    res = create_tmp_audiofile()
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


def first_few_sec_with_silence_and_beep(
    file: str, silence_to_add_sec: float = 0, few_sec: float = 1
) -> str:
    beep = ffmpeg.input("sine=d=0.5:f=800", f="lavfi")
    dur_sec = few_sec + silence_to_add_sec
    res = create_tmp_audiofile()
    ffmpeg.concat(
        beep,
        generate_silence(silence_to_add_sec),
        ffmpeg.input(file, to=dur_sec),
        a=1,
        v=0,
    ).output(res, audio_bitrate=get_bitrate(file), ar=44100).run(
        overwrite_output=True, capture_stdout=True, capture_stderr=True
    )
    return res


def pad_with_silence(
    file: str, add_begin_sec: float, add_end_sec: float, dir: str
) -> Any:
    parts = []
    if add_begin_sec > 0:
        parts.append(generate_silence(add_begin_sec))
    parts.append(ffmpeg.input(file))
    if add_end_sec > 0:
        parts.append(generate_silence(add_end_sec))
    return ffmpeg.concat(*parts, a=1, v=0).output(
        os.path.join(dir, os.path.basename(file)),
        audio_bitrate=get_bitrate(file),
        ar=44100,
    )


def play_file(file: str):
    player = vlc.MediaPlayer(file)
    player.play()
    time.sleep(get_duration_sec(file))


@functools.cache
def get_duration_sec(file: str) -> float:
    result = ffmpeg.probe(file)
    return float(result["format"]["duration"])


@functools.cache
def get_file_size(file: str) -> int:
    return os.stat(file).st_size


def get_bitrate(file: str) -> int:
    return int(ffmpeg.probe(file)["format"]["bit_rate"])


def how_much_silence_to_add_sec(
    file: str, min_silence_begin_sec: float, min_silence_end_sec: float
) -> tuple[float, float]:
    silences = get_silences(file)

    if len(silences) == 0:
        # there are no silences at all in the file
        return (min_silence_begin_sec, min_silence_end_sec)

    to_add_begin = 0
    first_silence = silences[0]
    # the first silence is not at the very start of the file -
    # fully pad file with silence.
    if silences[0].start_sec > 0.1:
        to_add_begin = min_silence_begin_sec
    else:
        to_add_begin = max(0, min_silence_begin_sec - first_silence.duration_sec)

    last_silence = silences[-1]
    duration = get_duration_sec(file)
    to_add_end = 0

    # the last silence is not at the end of the file
    if duration - last_silence.end_sec > 0.2:
        to_add_end = min_silence_end_sec
    else:
        to_add_end = max(0, min_silence_end_sec - last_silence.duration_sec)
    return (to_add_begin, to_add_end)


def maybe_pad_file_with_silence(
    file: str,
    out_directory: str,
    overwrite: bool,
    min_silence_begin_sec: float,
    min_silence_end_sec: float,
    play_paddings: bool,
) -> Any | None:
    """
    Given a file add silence to the end if it doesn't have enough.
    Returns ffmpeg stream or null if file doesnt need to be padded.
    """
    short_name = os.path.basename(file)
    if not overwrite and os.path.exists(os.path.join(out_directory, short_name)):
        logging.info(f"Skipping {short_name} as it already exists.")
        return None

    to_add_begin, to_add_end = how_much_silence_to_add_sec(
        file, min_silence_begin_sec, min_silence_end_sec
    )
    if to_add_begin == 0 and to_add_end == 0:
        logging.info(f"To file {short_name} no need to add silence")
        shutil.copy(file, out_directory)
        return None
    else:
        logging.info(
            f"Adding {to_add_begin}s to begin and {to_add_end}s to end silence to file {short_name}"
        )

    if play_paddings and to_add_begin > 0:
        preview = first_few_sec_with_silence_and_beep(file, to_add_begin)
        play_file(preview)

    if play_paddings and to_add_end > 0:
        preview = last_few_sec_with_silence_and_beep(file, to_add_end)
        play_file(preview)

    return pad_with_silence(
        file, add_begin_sec=to_add_begin, add_end_sec=to_add_end, dir=out_directory
    )


def pad_silence_to_files(
    files: Sequence[str],
    out_dir: str,
    overwrite: bool,
    min_silence_begin_sec: float,
    min_silence_end_sec: float,
    play_paddings: bool,
):
    """Takes a list of files and adds silence to the end if there is not enough
    silence."""
    audiofiles = sorted(files, key=get_file_size, reverse=True)
    scheduler = Scheduler()
    for file in audiofiles:
        stream = maybe_pad_file_with_silence(
            file,
            out_directory=out_dir,
            overwrite=overwrite,
            min_silence_begin_sec=min_silence_begin_sec,
            min_silence_end_sec=min_silence_end_sec,
            play_paddings=play_paddings,
        )
        if stream is not None:
            scheduler.enqueue_job(stream, file)
    scheduler.wait_till_all_finished()


def find_location_in_audiofile(file: str, start_location: float) -> float:
    player = vlc.MediaPlayer(file)
    player.play()
    location = start_location
    player.set_time(int(start_location * 1000))
    rate = 1
    left = "".join([chr(k) for k in [27, 91, 68]])
    right = "".join([chr(k) for k in [27, 91, 67]])
    up = "".join([chr(k) for k in [27, 91, 65]])
    down = "".join([chr(k) for k in [27, 91, 66]])
    while True:
        state = "playing" if player.is_playing() else "paused "
        print(
            f"\r{state}\t pos: {round(location, 1)}\trate: {round(rate, 1)}\t'y' to accept, 'r' to restart, left/right to change ",
            end="",
        )
        res = readchar.readkey()
        if res == "y":
            break
        elif res == "r":
            player.set_time(int(location * 1000))
        elif res == chr(32):
            player.pause() if player.is_playing() else player.play()
        elif res == left:
            location -= 0.1
            player.set_time(int(location * 1000))
        elif res == right:
            location += 0.1
            player.set_time(int(location * 1000))
        elif res == up:
            rate += 0.1
            player.set_rate(rate)
        elif res == down:
            rate -= 0.1
            player.set_rate(rate)
    player.stop()
    return location


def cut_files(
    files: Sequence[str],
    out_dir: str,
    overwrite: bool,
    first_n_sec: float,
    last_n_sec: float,
):
    """Cuts files removing first_n_sec and last_n_sec leaving the middle."""
    for file in files:
        short_name = os.path.basename(file)
        out_file = os.path.join(out_dir, short_name)
        if os.path.exists(out_file) and not overwrite:
            print(f"Skipping {short_name} as it already exists")
            continue
        print(f"\n\nCutting {short_name} file")
        duration = get_duration_sec(file)

        if first_n_sec != 0:
            first_position = find_location_in_audiofile(file, first_n_sec)
        else:
            first_position = 0
        if last_n_sec != 0:
            last_position = find_location_in_audiofile(file, duration - last_n_sec)
        else:
            last_position = duration
        ffmpeg.input(file, ss=first_position, to=last_position).output(
            out_file, audio_bitrate=get_bitrate(file), ar=44100
        ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
        real_duration = get_duration_sec(out_file)
        expected_duration = last_position - first_position
        if real_duration + 0.5 < real_duration:
            logging.error(
                f"Bad file ${short_name}. Expected ${expected_duration}s but got ${real_duration}s."
            )


def ensure_quality(
    files: Sequence[str], out_dir: str, overwrite: bool, min_bitrate: int
):
    audiofiles = sorted(files, key=get_file_size, reverse=True)
    scheduler = Scheduler()
    for file in audiofiles:
        short_name = os.path.basename(file)
        out_file = os.path.join(out_dir, short_name)
        if not overwrite and os.path.exists(out_file):
            logging.info(f"Skipping {short_name} as it already exists.")
            continue
        bitrate = get_bitrate(file)
        if bitrate >= min_bitrate:
            logging.info(f"Skipping {short_name} as it has bitrate {bitrate}")
            shutil.copy(file, out_dir)
            continue

        logging.info(f"Converting {short_name} because it has bitrate {bitrate}")
        stream = ffmpeg.input(file).output(
            out_file, audio_bitrate=min_bitrate, ar=44100
        )
        scheduler.enqueue_job(stream, file)
    scheduler.wait_till_all_finished()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audiobook tools.")
    parser.add_argument(
        "--operation",
        choices=[
            "pad_silence",
            "cut",
            "ensure_quality",
            "create_youtube",
            "create_podcast",
        ],
        required=True,
    )
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("audiofiles", nargs="*", metavar="001.mp3")
    parser.add_argument(
        "--first_n_sec",
        type=float,
        default=0,
        help="for 'cut' operation. Specifies audio length to from files from the beginning.",
    )
    parser.add_argument(
        "--last_n_sec",
        type=float,
        default=0,
        help="for 'cut' operation. Specifies audio length to from files from the end.",
    )
    parser.add_argument(
        "--overwrite",
        type=str2bool,
        default=False,
        help="Whether to overwrite existing files or skip if they already exist.",
    )
    parser.add_argument(
        "--min_silence_begin_sec",
        type=float,
        default=0.5,
        help="for 'pad_silence' operation. Specifies min amoun of silence at the beginning of a file. Default 0.5s",
    )
    parser.add_argument(
        "--min_silence_end_sec",
        type=float,
        default=2,
        help="for 'pad_silence' operation. Specifies min amoun of silence at the end of a file. Default 2s",
    )
    parser.add_argument(
        "--play_paddings",
        type=str2bool,
        default=False,
        help="for 'pad_silence' operation. Whether to play added paddings.",
    )
    parser.add_argument(
        "--min_bitrate",
        type=int,
        default=DEFAULT_BITRATE,
        help="for 'ensure_quality' operation. Specifies minimum bitrate. Files with smaller bitrate will be converted to match the minimum",
    )
    # Create books_vigeo_generator argument
    parser.add_argument(
        "--books_video_generator",
        help="path to the checked out version of https://github.com/vaukalak/books-video-generator",
    )
    parser.add_argument(
        "--youtube_video_type",
        choices=[i.value for i in YoutubeVideoType],
        help="When set to true generates full video (for free audiobooks). Otherwise uses the first chapter only for paid books.",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    if args.operation == "pad_silence":
        pad_silence_to_files(
            files=args.audiofiles,
            out_dir=args.out_dir,
            overwrite=args.overwrite,
            min_silence_begin_sec=args.min_silence_begin_sec,
            min_silence_end_sec=args.min_silence_end_sec,
            play_paddings=args.play_paddings,
        )
    elif args.operation == "cut":
        cut_files(
            files=args.audiofiles,
            out_dir=args.out_dir,
            first_n_sec=args.first_n_sec,
            last_n_sec=args.last_n_sec,
            overwrite=args.overwrite,
        )
    elif args.operation == "ensure_quality":
        ensure_quality(
            files=args.audiofiles,
            out_dir=args.out_dir,
            overwrite=args.overwrite,
            min_bitrate=args.min_bitrate,
        )
    elif args.operation == "create_youtube":
        if args.books_video_generator is None:
            logging.error("--books_video_generator is not provided.")
            return
        if args.youtube_video_type is None:
            logging.error("--youtube_video_type is not provided.")
            return
        create_youtube(
            out_dir=args.out_dir,
            books_video_generator=args.books_video_generator,
            video_type=args.youtube_video_type,
        )
    elif args.operation == "create_podcast":
        create_podcast(book_dir=args.out_dir)


if __name__ == "__main__":
    main()
