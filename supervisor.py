"""Restart the webhook server after syncing a successful self-update."""

from pathlib import Path
from shutil import which
import subprocess
import sys


def run():
    root = Path(__file__).resolve().parent
    uv = which("uv")
    if uv is None:
        print("Self-update requires uv on PATH.", file=sys.stderr)
        return 1
    while True:
        try:
            result = subprocess.run(
                [uv, "run", "--no-sync", "python", "main.py", "--serve"], cwd=root
            )
            if result.returncode != 75:
                return result.returncode
            result = subprocess.run([uv, "sync", "--locked"], cwd=root)
            if result.returncode != 0:
                print(
                    "Self-update dependency sync failed; server stopped.",
                    file=sys.stderr,
                )
                return result.returncode
        except KeyboardInterrupt:
            return 0
