"""Restart the webhook server after syncing a successful self-update."""

from collections.abc import Callable, Mapping
from pathlib import Path
from shutil import which
import os
import subprocess
import sys


def _is_executable(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.X_OK)


def _parent_exe(ppid: int) -> Path | None:
    try:
        return Path(f"/proc/{ppid}/exe").resolve()
    except OSError:
        return None


def resolve_uv(
    *,
    environ: Mapping[str, str] | None = None,
    which_fn: Callable[[str], str | None] = which,
    get_ppid: Callable[[], int] = os.getppid,
    parent_exe_fn: Callable[[int], Path | None] = _parent_exe,
) -> str | None:
    """Locate uv for self-update restarts.

    Prefer WEBHOOK_UPDATER_UV / UV, then PATH via which, then a parent
    process whose executable basename is uv (covers absolute ExecStart
    under systemd when PATH does not include uv).
    """
    env = os.environ if environ is None else environ
    for key in ("WEBHOOK_UPDATER_UV", "UV"):
        candidate = env.get(key)
        if candidate and _is_executable(candidate):
            return str(Path(candidate))

    found = which_fn("uv")
    if found and _is_executable(found):
        return found

    parent = parent_exe_fn(get_ppid())
    if parent is not None and parent.name == "uv" and _is_executable(parent):
        return str(parent)
    return None


def run():
    root = Path(__file__).resolve().parent
    uv = resolve_uv()
    if uv is None:
        print(
            "Self-update requires uv; set PATH or WEBHOOK_UPDATER_UV "
            "to the uv binary.",
            file=sys.stderr,
        )
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
