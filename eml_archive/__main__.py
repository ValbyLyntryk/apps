"""CLI for the local EML archive viewer."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .crash import configure_stdio, report_crash, safe_print
from .demo import write_demo_archive
from .indexer import Indexer
from .server import App, serve
from .store import Store, preferred_db_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="View, search, and tag archived .eml files without moving them."
    )
    p.add_argument(
        "--archive",
        help="Folder on the (network) drive that contains the .eml files",
    )
    p.add_argument(
        "--db",
        help="SQLite index file or folder. Default: archive.db next to the .exe (shared when the .exe lives on the NAS)",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="Do not open a web browser")
    p.add_argument("--reindex", action="store_true", help="Index the archive, then exit")
    p.add_argument("--full", action="store_true", help="Re-read every file even if unchanged")
    p.add_argument(
        "--repair-empty",
        action="store_true",
        help="Re-parse only indexed emails with empty bodies, then exit (not a full reindex)",
    )
    p.add_argument("--demo", action="store_true", help="Open a small built-in sample archive")
    p.add_argument("--version", action="version", version=f"email-archive {__version__}")
    return p


def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.demo:
        demo_root = Path(tempfile.mkdtemp(prefix="eml-archive-demo-"))
        write_demo_archive(demo_root)
        db_path = demo_root / "index.db"
        archive = demo_root
        safe_print(f"Demo archive: {demo_root}", file=sys.stderr)
    else:
        db_path = preferred_db_path(args.db)
        archive = Path(args.archive).expanduser() if args.archive else None

    store = Store(db_path)
    if archive is not None:
        store.set_archive_root(str(archive.resolve()))

    if args.repair_empty:
        root = archive or Path(store.archive_root())
        if not root or not str(root):
            safe_print("Pass --archive /path/to/emails (needed to resolve relative folders)", file=sys.stderr)
            return 2
        indexer = Indexer(store)
        indexer.run(Path(root), repair_empty=True)
        snap = indexer.snapshot()
        safe_print(
            f"Repaired {snap['updated']} empty bodies "
            f"({snap['errors']} errors). Not a full reindex.",
            file=sys.stderr,
        )
        return 0 if snap.get("phase") in {"done"} else 1

    if args.reindex:
        root = archive or Path(store.archive_root())
        if not root or not str(root):
            safe_print("Pass --archive /path/to/emails", file=sys.stderr)
            return 2
        indexer = Indexer(store)
        indexer.run(Path(root), full=args.full)
        snap = indexer.snapshot()
        safe_print(
            f"Indexed {snap['updated']} files "
            f"({snap['skipped']} unchanged, {snap['errors']} errors, {snap['removed']} removed)",
            file=sys.stderr,
        )
        return 0 if snap.get("phase") in {"done"} else 1

    app = App(store)
    open_browser = not args.no_browser and os.environ.get("EMAIL_ARCHIVE_NO_BROWSER") != "1"
    httpd = serve(
        app,
        host=args.host,
        port=args.port,
        open_browser=open_browser,
    )
    if httpd is None:
        if open_browser:
            safe_print("Email archive is already running. Opened it in your browser.", file=sys.stderr)
        else:
            safe_print("Email archive is already running.", file=sys.stderr)
        return 0

    # Bind first so the existing index can be shown, then scan in the background.
    if args.demo:
        app.indexer.start(Path(store.archive_root()), full=True)
    else:
        root = archive or (Path(store.archive_root()) if store.archive_root() else None)
        if root and Path(root).exists():
            app.indexer.start(Path(root), full=args.full)
    url = f"http://{args.host}:{httpd.server_port}/"
    safe_print("", file=sys.stderr)
    safe_print("  Email archive is running.", file=sys.stderr)
    safe_print(f"  Open {url}", file=sys.stderr)
    safe_print("  Keep this window open while you read mail. Close it to quit.", file=sys.stderr)
    safe_print(f"  Index file: {store.db_path}", file=sys.stderr)
    safe_print("", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        safe_print("\nStopped.", file=sys.stderr)
    finally:
        httpd.server_close()
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    try:
        return _run(argv)
    except SystemExit:
        raise
    except Exception as exc:
        report_crash(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
