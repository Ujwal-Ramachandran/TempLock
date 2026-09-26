"""Tiny progress helpers so long steps visibly show they are alive.

These are cosmetic only. They write to a stream (stdout by default) and never
change what a function returns. In a real terminal they animate a spinner with
an elapsed-second counter; when output is redirected or captured (no TTY) they
fall back to plain start/done lines so logs stay readable.
"""

from __future__ import annotations

import itertools
import sys
import threading
import time
from contextlib import contextmanager


@contextmanager
def spinner(label: str, stream=None):
    """Show an animated 'working' indicator while the wrapped block runs.

    Usage:
        with spinner("generating with model-x"):
            subprocess.run(...)   # long, blocking call

    The spinner runs in a background thread and is cleared on exit, so whatever
    the caller prints next starts on a clean line.
    """
    stream = stream or sys.stdout

    # No TTY (piped/captured): keep it simple and log-friendly.
    if not hasattr(stream, "isatty") or not stream.isatty():
        start = time.time()
        stream.write(f"  {label} ...\n")
        stream.flush()
        try:
            yield
        finally:
            stream.write(f"  {label} done in {time.time() - start:.1f}s\n")
            stream.flush()
        return

    stop = threading.Event()
    start = time.time()
    frames = itertools.cycle("|/-\\")

    def _animate() -> None:
        while not stop.is_set():
            stream.write(f"\r  {next(frames)} {label} ({time.time() - start:.0f}s)   ")
            stream.flush()
            time.sleep(0.1)

    thread = threading.Thread(target=_animate, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        # Clear the spinner line so the next print is clean.
        stream.write("\r" + " " * (len(label) + 28) + "\r")
        stream.flush()


def progress_line(current: int, total: int, label: str, stream=None) -> None:
    """Overwrite a single line with 'label current/total'. Call repeatedly."""
    stream = stream or sys.stderr
    end = "\n" if current >= total else ""
    stream.write(f"\r  {label} {current}/{total}   {end}")
    stream.flush()
