import datetime
import logging
import os
import shutil
import subprocess


import ffmpeg
from book import Book
from feedgen.feed import FeedGenerator
from feedgen.ext.podcast import PodcastExtension
import belorthography


def create_podcast(book_dir: str):
    """Given a book generates podcast files: mp3 files and rss.xml.

    Args:
        book_dir: Directory where the book is located."""
    book = Book(book_dir)
    assert book.cover_image.endswith(".png") or book.cover_image.endswith(
        ".jpg"
    ), f"Cover image must be .png or .jpg because rss library allows only these formats. Got {book.cover_image} instead."

    # create podcast dir
    podcast_dir = os.path.join(book.dir, "podcast")
    os.makedirs(podcast_dir, exist_ok=True)

    logging.info("Copying and resizing cover")
    subprocess.run(
        [
            "convert",
            book.cover_image,
            "-resize",
            "1400x1400",
            os.path.join(podcast_dir, os.path.basename(book.cover_image)),
        ],
    )

    logging.info("Copying mp3 files to podcast dir")
    for file in book.audio_files:
        basename = os.path.basename(file)
        logging.info(f"Processing {basename}")
        if file == book.audio_files[0]:
            logging.info("Concatenating intro and first chapter. May take a while.")
            _concat_files(
                [book.get_intro_file(), file], os.path.join(podcast_dir, basename)
            )
        elif file == book.audio_files[-1]:
            logging.info("Concatenating last chapter and outro. May take a while.")
            _concat_files(
                [file, book.get_outro_file()], os.path.join(podcast_dir, basename)
            )
        else:
            shutil.copy(file, podcast_dir)

    logging.info("Generating rss.xml")
    _generate_rss(book, podcast_dir)


def _generate_rss(book: Book, podcast_dir: str):
    gcs_folder_name = _generate_gcs_folder_name(book)
    full_gcs_path = f"storage.googleapis.com/belaudiobooks/{gcs_folder_name}"
    fg = FeedGenerator()
    fg.load_extension("podcast")
    podcast: PodcastExtension = fg.podcast

    fg.title(book.metadata.title)

    description = book.metadata.description + "\n\n"
    if len(book.metadata.translators) > 0:
        description += f"Пераклад: {', '.join(book.metadata.translators)}\n"
    if len(book.metadata.narrators) > 0:
        verb = "Чытае" if len(book.metadata.narrators) == 1 else "Чытаюць"
        description += f"{verb}: {', '.join(book.metadata.narrators)}"
    fg.description(description)

    fg.link(href="https://audiobooks.by")
    fg.ttl(60)
    fg.pubDate(datetime.datetime.now(tz=datetime.timezone.utc))
    fg.language("be")
    fg.copyright("All rights reserved")
    podcast.itunes_summary(description)
    podcast.itunes_subtitle(book.metadata.title)
    podcast.itunes_category("Fiction")
    image_url = f"https://{full_gcs_path}/{os.path.basename(book.cover_image)}"
    fg.image(url=image_url, title=book.metadata.title)
    podcast.itunes_image(image_url)

    podcast.itunes_author(", ".join(book.metadata.authors))
    podcast.itunes_owner(name="Mikita Belahlazau", email="belaudiobooks@gmail.com")
    podcast.itunes_type("serial")
    podcast.itunes_explicit("no")
    podcast.itunes_complete("yes")

    first_pub_date = datetime.datetime.now(
        tz=datetime.timezone.utc
    ) - datetime.timedelta(days=len(book.metadata.chapters))
    for i, title in enumerate(book.metadata.chapters):
        entry = fg.add_entry()
        entry.guid(f"{gcs_folder_name}_{i}", permalink=True)
        entry.title(title)
        podtrac_prefix = "http://www.podtrac.com/pts/redirect.mp3"
        file = os.path.join(podcast_dir, os.path.basename(book.audio_files[i]))
        entry.enclosure(
            f"{podtrac_prefix}/{full_gcs_path}/{os.path.basename(file)}",
            # get file size in bytes
            os.path.getsize(file),
            "audio/mpeg",
        )
        entry.podcast.itunes_episode(i + 1)
        entry.podcast.itunes_duration(_get_duration(file))
        entry.pubDate(first_pub_date)
        first_pub_date += datetime.timedelta(days=1)
    fg.rss_file(os.path.join(podcast_dir, "rss.xml"), pretty=True, encoding="utf-8")
    logging.info(f"GCS folder name is: {gcs_folder_name}")
    logging.info(
        "DON'T FORGET TO UPDATE CATEGORIES AND SUBTITLE TAGS. CATEGORIES: https://podcasters.apple.com/support/1691-apple-podcasts-categories"
    )


def _generate_gcs_folder_name(book: Book) -> str:
    name = " ".join(book.metadata.authors) + " " + book.metadata.title
    name = name.lower()
    name = belorthography.convert(
        name,
        belorthography.Orthography.OFFICIAL,
        belorthography.Orthography.LATIN_NO_DIACTRIC,
    )
    name = name.replace(" ", "_")
    return name


def _get_bitrate(file: str) -> int:
    return int(ffmpeg.probe(file)["format"]["bit_rate"])


def _get_duration(file: str) -> str:
    result = ffmpeg.probe(file)
    sec = float(result["format"]["duration"])
    return str(datetime.timedelta(seconds=int(sec)))


def _concat_files(files: list[str], output: str):
    inputs = [ffmpeg.input(file) for file in files]
    ffmpeg.concat(*inputs, a=1, v=0).output(
        output,
        audio_bitrate=_get_bitrate(files[0]),
        ar=44100,
    ).run(overwrite_output=True, quiet=True)
