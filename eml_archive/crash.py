"""Crash logging so a frozen Windows exe does not just flash and vanish."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            continue


def safe_print(*args: object, **kwargs: object) -> None:
    try:
        file = kwargs.get("file", sys.stderr)
        if file is None:
            return
        print(*args, **kwargs)  # type: ignore[arg-type]
        flush = getattr(file, "flush", None)
        if callable(flush):
            flush()
    except Exception:
        return


def crash_log_path() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        try:
            candidates.append(Path(sys.executable).resolve().parent / "EmailArchive-crash.log")
        except OSError:
            pass
    candidates.append(Path.home() / ".email-archive" / "EmailArchive-crash.log")
    candidates.append(Path(tempfile.gettempdir()) / "EmailArchive-crash.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8"):
                return path
        except OSError:
            continue
    return candidates[-1]


def report_crash(exc: BaseException, *, pause: bool = True) -> Path | None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path: Path | None
    try:
        path = crash_log_path()
        path.write_text(text, encoding="utf-8")
    except OSError:
        path = None
    safe_print(text, file=sys.stderr)
    if os.name == "nt":
        lines = [str(exc)]
        if path is not None:
            lines.extend(["", "Details saved to:", str(path)])
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, "\n".join(lines), "Email archive failed to start", 0x10)
        except Exception:
            pass
        if pause:
            try:
                if sys.stdin and sys.stdin.isatty():
                    input("Press Enter to close...")
                else:
                    time.sleep(6)
            except Exception:
                time.sleep(2)
    return path
