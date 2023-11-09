from collections.abc import MutableSequence, MutableMapping
import functools
import logging
import multiprocessing
import os
import re
import subprocess
import threading
import time
from typing import Any

import ffmpeg


@functools.cache
def get_duration_sec(file: str) -> float:
    result = ffmpeg.probe(file)
    return float(result["format"]["duration"])


class Scheduler:
    """Class that runs ffmpeg stream operations in parallel. 
    It runs N-1 thread where N is number of cores and keeps 
    track of progress in each thread."""
    def __init__(self):
        self._waiting_jobs: MutableSequence[tuple[Any, str]] = []
        self._running_threads: MutableSequence[threading.Thread] = []
        self._lock = threading.Lock()
        self._current_progress: MutableMapping[str, int] = {}

    def enqueue_job(self, stream: Any, file: str):
        self._waiting_jobs.append((stream, file))
        self._maybe_run_job()

    def wait_till_all_finished(self):
        while True:
            self._clean_threads()
            self._lock.acquire()
            should_exit = (
                len(self._waiting_jobs) == 0 and len(self._running_threads) == 0
            )
            self._lock.release()
            if should_exit:
                break
            self._print_progress()
            time.sleep(2)
        logging.info("All jobs are done")

    def _clean_threads(self):
        self._lock.acquire()
        self._running_threads = [
            thread for thread in self._running_threads if thread.is_alive()
        ]
        self._lock.release()


    def _maybe_run_job(self):
        self._clean_threads()
        self._lock.acquire()
        max_threads = max(multiprocessing.cpu_count() - 1, 1)
        if len(self._waiting_jobs) > 0 and len(self._running_threads) < max_threads:
            stream, file = self._waiting_jobs.pop(0)
            process = stream.run_async(overwrite_output=True, quiet=True)
            new_thread = threading.Thread(
                name=f"audiotools-{os.path.basename(file)}",
                target=self._wait_till_finished,
                args=(process, file),
            )
            new_thread.start()
            self._running_threads.append(new_thread)
        self._lock.release()

    def _wait_till_finished(self, process: subprocess.Popen, file: str):
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
                    100
                    * (int(hour) * 3600 + int(min) * 60 + int(sec))
                    / expected_duration
                )
                self._lock.acquire()
                self._current_progress[short_name] = progress
                self._lock.release()
            if process.poll() == 0:
                break
        self._lock.acquire()
        self._current_progress[short_name] = 100
        self._lock.release()
        self._maybe_run_job()

    def _print_progress(self):
        self._lock.acquire()
        progress = self._current_progress
        progress_str = [f"{file}: {progress}%" for file, progress in progress.items()]
        for file in list(progress.keys()):
            if progress[file] == 100:
                progress.pop(file)
        logging.info(
            f"Progress. Enqueued {len(self._waiting_jobs)} jobs. Running: "
            + "\t".join(progress_str)
        )
        self._lock.release()
