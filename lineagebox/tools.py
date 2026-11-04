"""Running the programs LineageBox uses (ffmpeg and ffprobe for videos, ExifTool for writing photo files), so that a
program that fails, hangs or isn't there is reported the same way, in a sentence, from every one of them.
"""
import os
import subprocess
from pathlib import Path


class Error(Exception):
    """The program could not read or write this file: it is damaged, or not one the program knows. The message is the
    program's own last line about it."""


class Unavailable(Error):
    """The program isn't there (or can't be run): nothing is wrong with the file."""


class TimedOut(Error):
    """The program took longer than it should: a slow disk or share, or a file it struggles with. Tried again later."""


def run(args: list[str], timeout: float | None = 300, nice: bool = False) -> bytes:
    """What the program (args[0]) writes out. nice: at a lower priority, for long work on a machine that also serves
    the site."""
    name = Path(args[0]).name
    if nice and hasattr(os, "nice"):
        args = ["nice", "-n", "10", *args]
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise Unavailable(f"{name} could not be run: {e.strerror or e}") from None
    except subprocess.TimeoutExpired:
        raise TimedOut(f"{name} took over {timeout:.0f} s") from None
    if p.returncode:
        message = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Error(message[-1] if message else f"{name} stopped with exit code {p.returncode}")
    return p.stdout
