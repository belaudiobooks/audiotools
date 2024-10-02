from enum import StrEnum
import logging
import os
import re
import shutil
import subprocess

from book import Book


class YoutubeVideoType(StrEnum):
    # Full book containing all chapters with full description.
    FREE = "free"
    # Only the first chapter with full description.
    PAID = "paid"
    # Full book without intro and with "mystery" description.
    MYSTERY_BOOK = "mystery"


def create_youtube(
    out_dir: str, books_video_generator: str, video_type: YoutubeVideoType
):
    """Given a book generates a video for youtube.

    Args:
        out_dir: Directory where the book is located.
        books_video_generator: Path to the https://github.com/vaukalak/books-video-generator
          repo checked out locally.
        video_type: Type of video to generate. See YoutubeVideoType."""
    book = Book(out_dir)
    book_name = "from_tools"
    env = {}
    env.update(os.environ)
    env.update({"BOOK": book_name})
    book_resource = os.path.join(books_video_generator, "resource", book_name)
    if os.path.exists(book_resource):
        shutil.rmtree(book_resource)
    if not os.path.exists(os.path.join(book.dir, "youtube_config.yml")):
        copied_sample = os.path.join(book.dir, "youtube_config.yml")
        shutil.copy(
            os.path.join(books_video_generator, "sample/config.yml"),
            copied_sample,
        )
        logging.error(
            f"youtube_config.yml is missing, copied sample from books-video-generator to {copied_sample}. "
            + "please check the config file and update necessary fields"
        )
        return
    subprocess.run(
        ["./add-book.ts"],
        cwd=books_video_generator,
        env=env,
    )
    # copy files to audio folder
    for file in book.audio_files:
        shutil.copy(file, os.path.join(book_resource, "audio"))
    if video_type != YoutubeVideoType.MYSTERY_BOOK:
        shutil.copy(
            book.get_intro_file(), os.path.join(book_resource, "audio", "00.mp3")
        )
    os.remove(os.path.join(book_resource, "background.jpg"))
    subprocess.run(
        [
            "convert",
            book.youtube_image,
            "-resize",
            "1920x1080",
            os.path.join(book_resource, "background.png"),
        ],
    )
    shutil.copy(
        os.path.join(book.dir, "youtube_config.yml"),
        os.path.join(book_resource, "config.yml"),
    )
    _generate_chapters_csv(video_type, book, book_resource)

    subprocess.run(
        ["./generate-chapters.ts"],
        cwd=books_video_generator,
        env=env,
    )
    subprocess.run(
        ["./concat-chapters.ts"],
        cwd=books_video_generator,
        env=env,
    )
    timecodes = subprocess.check_output(
        ["./create-timecodes.ts"],
        cwd=books_video_generator,
        env=env,
    ).decode("utf-8")
    with open(os.path.join(book.dir, "youtube_description.txt"), "w") as f:
        f.write(_generate_video_description(video_type, book, timecodes))
    shutil.copy(
        os.path.join(books_video_generator, "out", book_name, "output.mkv"), book.dir
    )


def _generate_video_description(
    video_type: YoutubeVideoType, book: Book, timecodes: str
) -> str:
    description: list[str] = []
    authors = ", ".join(book.metadata.authors)
    if video_type == YoutubeVideoType.FREE:
        description.append(f"«{book.metadata.title}» {authors}")
    elif video_type == YoutubeVideoType.PAID:
        description.append(f"«{book.metadata.title}» {authors}. Першая частка")
    elif video_type == YoutubeVideoType.MYSTERY_BOOK:
        description.append("Аўдыякніга-неспадзяванка #INSERT_ME")
    else:
        raise ValueError(f"Unknown video type {video_type}")
    description.append("")
    if video_type == YoutubeVideoType.FREE:
        description.append(
            "Слухаць на іншых пляцоўках: https://audiobooks.by/books/INSERT_ME!!!\n"
        )
    elif video_type == YoutubeVideoType.PAID:
        description.append("Слухаць цалкам: https://audiobooks.by/books/INSERT_ME!!!\n")

    if video_type == YoutubeVideoType.MYSTERY_BOOK:
        description.append(
            "\n\n".join(
                [
                    "Пра што гэта аўдыякніга? Як яна называецца? Хто аўтар?",
                    "🤷 Трэба яе праслухаць, каб даведацца.",
                    "Мы выклалі адну з нашых платных аўдыякніг на ютуб. Без назвы і аўтара. Каб вы маглі паслухаць кнігу не ведаючы пра яе практычна нічога. Ніякіх папярэдніх стэрэатыпаў ці чаканняў. Проста аўдыякніга і вы.",
                    "Напрыканцы месяца аўдыякніга будзе выдалена і мы абвесцім назву кнігі ў нашых сацсетках.",
                    "https://instagram.com/audiobooks.by",
                    "https://t.me/bel_audiobooks",
                ]
            )
        )
    else:
        description.append(book.metadata.description)
        if len(book.metadata.narrators) > 0:
            verb = "Чытае: " if len(book.metadata.narrators) == 1 else "Чытаюць: "
            description.append(verb + ", ".join(book.metadata.narrators))
        if len(book.metadata.translators) > 0:
            description.append("Пераклад: " + ", ".join(book.metadata.translators))
    description.append("")
    description += [
        line for line in timecodes.splitlines() if re.match(r"^\d+:\d+:\d+ .*", line)
    ]
    return "\n".join(description)


def _generate_chapters_csv(video_type: YoutubeVideoType, book: Book, out_dir: str):
    add_chapters = video_type == YoutubeVideoType.FREE
    with open(os.path.join(out_dir, "chapters.csv"), "w") as f:
        f.write("ID,Назва\n")
        if video_type != YoutubeVideoType.MYSTERY_BOOK:
            intro_name = "Уступ" if add_chapters else ""
            f.write(f"0,{intro_name}\n")
        for i, chapter in enumerate(book.metadata.chapters):
            chapter_name = ""
            if video_type == YoutubeVideoType.FREE:
                chapter_name = chapter
            elif video_type == YoutubeVideoType.MYSTERY_BOOK:
                chapter_name = f"Частка {i + 1}"
            f.write(f'{i+1},"{chapter_name}"\n')
            # For paid books we give provide only the first chapter on youtube.
            print(i)
            if video_type == YoutubeVideoType.PAID and i > 1:
                break
