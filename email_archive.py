#!/usr/bin/env python3
"""View, search, and organize archived .eml files without moving them.

    python3 email_archive.py --archive "/path/to/network/drive/emails"
    python3 email_archive.py --demo

On Windows you can also freeze this into a portable .exe:

    python build_exe.py
"""

from __future__ import annotations

from multiprocessing import freeze_support

from eml_archive.__main__ import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
