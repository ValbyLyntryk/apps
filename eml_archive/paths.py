"""Resolve package files whether we are running from source or a frozen exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def meipass_dir() -> Path | None:
    raw = getattr(sys, "_MEIPASS", None)
    return Path(raw) if raw else None


def package_root() -> Path:
    """Directory that contains ``static/`` and the Python package files."""
    if is_frozen():
        base = meipass_dir() or Path(sys.executable).resolve().parent
        for candidate in (base / "eml_archive", base):
            if (candidate / "static" / "index.html").is_file():
                return candidate
        return base / "eml_archive"
    return Path(__file__).resolve().parent


def static_dir() -> Path:
    return package_root() / "static"


def install_dir() -> Path:
    """Folder that contains the .exe (or email_archive.py). Shared on a NAS."""
    override = os.environ.get("EMAIL_ARCHIVE_INSTALL_DIR", "").strip()
    if override:
        return Path(override)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
