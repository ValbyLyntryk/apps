"""CLI for the local EML archive viewer."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .demo import write_demo_archive
from .indexer import Indexer
from .server import App, serve
from .store import Store, default_db_path


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
        help=f"SQLite index location (default: {default_db_path()})",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="Do not open a web browser")
    p.add_argument("--reindex", action="store_true", help="Index the archive, then exit")
    p.add_argument("--full", action="store_true", help="Re-read every file even if unchanged")
    p.add_argument("--demo", action="store_true", help="Open a small built-in sample archive")
    p.add_argument("--version", action="version", version=f"email-archive {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db).expanduser() if args.db else default_db_path()
    if args.demo:
        demo_root = Path(tempfile.mkdtemp(prefix="eml-archive-demo-"))
        write_demo_archive(demo_root)
        db_path = demo_root / "index.db"
        archive = demo_root
        print(f"Demo archive: {demo_root}", file=sys.stderr)
    else:
        archive = Path(args.archive).expanduser() if args.archive else None

    store = Store(db_path)
    if archive is not None:
        store.set_archive_root(str(archive.resolve()))

    if args.reindex:
        root = archive or Path(store.archive_root())
        if not root or not str(root):
            print("Pass --archive /path/to/emails", file=sys.stderr)
            return 2
        indexer = Indexer(store)
        indexer.run(Path(root), full=args.full)
        snap = indexer.snapshot()
        print(
            f"Indexed {snap['updated']} files "
            f"({snap['skipped']} unchanged, {snap['errors']} errors, {snap['removed']} removed)",
            file=sys.stderr,
        )
        return 0 if snap.get("phase") in {"done"} else 1

    app = App(store)
    if archive is not None and not args.demo:
        app.indexer.start(Path(archive), full=args.full)
    elif args.demo:
        app.indexer.start(Path(store.archive_root()), full=True)

    httpd = serve(
        app,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser and os.environ.get("EMAIL_ARCHIVE_NO_BROWSER") != "1",
    )
    url = f"http://{args.host}:{httpd.server_port}/"
    print(f"Email archive viewer {__version__}", file=sys.stderr)
    print(f"Open {url}", file=sys.stderr)
    print("Emails stay on the drive. Index + tags are stored in:", file=sys.stderr)
    print(f"  {store.db_path}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        httpd.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
