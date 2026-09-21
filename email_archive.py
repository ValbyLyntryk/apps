#!/usr/bin/env python3
"""View, search, and organize archived .eml files without moving them.

    python3 email_archive.py --archive "/path/to/network/drive/emails"
    python3 email_archive.py --demo
"""

from __future__ import annotations

from eml_archive.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
