from dataclasses import dataclass
import os
import re
from typing import Literal


@dataclass
class BookMetadata:
    title: str
    authors: list[str]
    narrators: list[str]
    translators: list[str]
    description: str
    chapters: list[str]


class Book:
    def __init__(self, dir):
        self.dir = dir
        self._validate()
        self.cover_image = self._get_image("art")
        self.youtube_image = self._get_image("youtube")
        self.metadata = self._parse_metadata()
        self.audio_files = self._get_audio_files()
        if len(self.audio_files) != len(self.metadata.chapters):
            raise ValueError(
                f"Number of audio files ({len(self.audio_files)}) does not match number of chapters ({len(self.metadata.chapters)})"
            )

    def _validate_file_exists(self, file):
        if not os.path.exists(os.path.join(self.dir, file)):
            raise FileNotFoundError(f"File {file} does not exist in {self.dir}")

    def _validate(self):
        # Check that dir contains description.txt, ex-1.mp3, ex-2.mp3, art.png and youtube.png
        if not os.path.exists(self.dir):
            raise FileNotFoundError(f"Directory {self.dir} does not exist")
        self._validate_file_exists("description.txt")
        self._validate_file_exists("ex-1.mp3")
        self._validate_file_exists("ex-2.mp3")

    def get_intro_file(self) -> str:
        return os.path.join(self.dir, "ex-1.mp3")

    def get_outro_file(self) -> str:
        return os.path.join(self.dir, "ex-2.mp3")

    def _get_audio_files(self) -> list[str]:
        files = [file for file in os.listdir(self.dir) if re.match(r"\d+.*.mp3", file)]
        files = sorted(files, key=lambda x: int(os.path.splitext(x)[0].split(" ")[0]))
        return [os.path.join(self.dir, file) for file in files]

    def _parse_metadata(self) -> BookMetadata:
        data = {
            "Апісанне": "",
            "Змест": [],
        }
        with open(os.path.join(self.dir, "description.txt")) as f:
            lines = f.readlines()

            type: Literal["short_data", "description", "chapters"] = "short_data"
            for line in lines:
                if type == "short_data":
                    if line.strip() == "":
                        type = "description"
                    elif line.count(": ") == 0:
                        raise ValueError(
                            f"Invalid line in short data: {line}. Expected ':'."
                        )
                    else:
                        key, value = line.strip().split(": ")
                        data[key] = value
                elif type == "description":
                    if line.strip() == "Змест:":
                        type = "chapters"
                    else:
                        data["Апісанне"] += line.strip() + "\n"
                elif type == "chapters":
                    chapter = line.strip()
                    if chapter != "":
                        data["Змест"].append(chapter)

        def maybe_split_empty(x: str):
            return [] if len(x) == 0 else x.split(", ")

        return BookMetadata(
            title=data["Назва"],
            authors=maybe_split_empty(data.get("Аўтар", "")),
            narrators=maybe_split_empty(data.get("Чытае", "")),
            translators=maybe_split_empty(data.get("Пераклад", "")),
            description=data["Апісанне"].strip(),
            chapters=data["Змест"],
        )

    def _get_image(self, file) -> str:
        # Find image file with one of extendsions: .png, .jpg, .jpeg and return
        for ext in ["png", "jpg", "jpeg"]:
            image = os.path.join(self.dir, file + "." + ext)
            if os.path.exists(image):
                return image
        raise FileNotFoundError(f"Image file for {file} not found")
