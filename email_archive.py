#!/usr/bin/env python3
"""View, search, and organize archived .eml files without moving them.

    python3 email_archive.py --archive "/path/to/network/drive/emails"
    python3 email_archive.py --demo

On Windows you can also freeze this into a portable .exe:

    python build_exe.py
"""

from __future__ import annotations

if __name__ == "__main__":
    # Import the app only in the main process. Avoid the multiprocessing
    # module here: pulling it in has made frozen Windows one-file apps
    # exit immediately on open.
    from eml_archive.__main__ import main

    raise SystemExit(main())
