"""Scan a folder tree for .eml files and refresh the local SQLite index."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .parser import ParseError, parse_eml_file
from .store import Store

ProgressCb = Callable[[dict[str, Any]], None]

EML_SUFFIXES = {".eml", ".emlx"}


def iter_eml_files(root: Path) -> list[Path]:
    files: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix in EML_SUFFIXES:
                files.append(Path(dirpath) / name)
    return files


def _walk(root: Path):
    try:
        yield from os_walk(root)
    except OSError:
        return


def os_walk(root: Path):
    import os

    return os.walk(root)


def rel_folder(root: Path, file_path: Path) -> str:
    try:
        rel = file_path.parent.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    as_posix = rel.as_posix()
    return "" if as_posix == "." else as_posix


class Indexer:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = threading.Lock()
        self.status: dict[str, Any] = {
            "running": False,
            "phase": "idle",
            "processed": 0,
            "total": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "removed": 0,
            "current": "",
            "error_samples": [],
            "started_at": None,
            "finished_at": None,
            "mode": "index",
        }
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.status)

    def _set(self, **kwargs: Any) -> None:
        with self.lock:
            self.status.update(kwargs)

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, root: Path, full: bool = False, repair_empty: bool = False) -> bool:
        with self.lock:
            if self.status.get("running"):
                return False
            self.status["running"] = True
            self.status["phase"] = "starting"
            self.status["mode"] = "repair_empty" if repair_empty else "index"
            self.status["error_samples"] = []
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self.run,
            args=(root, full, repair_empty),
            name="eml-indexer",
            daemon=True,
        )
        self._thread.start()
        return True

    def run(self, root: Path, full: bool = False, repair_empty: bool = False) -> None:
        started = int(time.time())
        self._set(
            running=True,
            phase="repairing empty bodies" if repair_empty else "scanning",
            mode="repair_empty" if repair_empty else "index",
            processed=0,
            total=0,
            updated=0,
            skipped=0,
            errors=0,
            removed=0,
            current="",
            started_at=started,
            finished_at=None,
        )
        try:
            root = Path(root).expanduser() if root else Path(".")
            if repair_empty:
                self._repair_empty_bodies(root)
                return
            if not root.exists() or not root.is_dir():
                self._set(
                    running=False,
                    phase="error",
                    current=f"Folder not found: {root}",
                    finished_at=int(time.time()),
                )
                return
            files = iter_eml_files(root)
            self._set(total=len(files), phase="indexing")
            previous = {} if full else self.store.unchanged_paths(root)
            keep: set[str] = set()
            updated = skipped = errors = 0
            samples: list[str] = []

            for i, path in enumerate(files, start=1):
                if self._cancel.is_set():
                    self._set(phase="cancelled", running=False, finished_at=int(time.time()))
                    return
                abs_path = str(path.resolve())
                keep.add(abs_path)
                self._set(processed=i, current=abs_path)
                try:
                    mtime_ns = path.stat().st_mtime_ns
                except OSError as exc:
                    errors += 1
                    if len(samples) < 8:
                        samples.append(f"{path}: {exc}")
                    self._set(errors=errors, error_samples=list(samples))
                    continue
                if not full and previous.get(abs_path) == mtime_ns:
                    skipped += 1
                    self._set(skipped=skipped)
                    continue
                try:
                    rec = parse_eml_file(path)
                    rec["path"] = abs_path
                    rec["filename"] = path.name
                    rec["folder"] = rel_folder(root, path)
                    with self.store.lock:
                        self.store.upsert_email(rec)
                    updated += 1
                    if updated % 25 == 0:
                        with self.store.lock:
                            self.store.commit()
                    self._set(updated=updated)
                except (ParseError, OSError, ValueError) as exc:
                    errors += 1
                    if len(samples) < 8:
                        samples.append(f"{path}: {exc}")
                    self._set(errors=errors, error_samples=list(samples))

            with self.store.lock:
                self.store.commit()
                removed = self.store.delete_missing(keep, str(root.resolve()))
                self.store.commit()
                self.store.set_archive_root(str(root.resolve()))
                self.store.set_meta("last_index", str(int(time.time())))
            self._set(
                running=False,
                phase="done",
                updated=updated,
                skipped=skipped,
                errors=errors,
                removed=removed,
                current="",
                finished_at=int(time.time()),
            )
        except Exception as exc:  # noqa: BLE001 — surface indexer crashes in the UI
            self._set(
                running=False,
                phase="error",
                current=str(exc),
                finished_at=int(time.time()),
            )

    def _repair_empty_bodies(self, root: Path) -> None:
        """Re-parse only indexed rows with empty body_text. Does not walk the tree."""
        rows = self.store.empty_body_paths()
        self._set(total=len(rows), phase="repairing empty bodies", mode="repair_empty")
        updated = skipped = errors = 0
        samples: list[str] = []
        for i, (abs_path, folder) in enumerate(rows, start=1):
            if self._cancel.is_set():
                self._set(phase="cancelled", running=False, finished_at=int(time.time()))
                return
            path = Path(abs_path)
            self._set(processed=i, current=abs_path)
            if not path.is_file():
                errors += 1
                if len(samples) < 8:
                    samples.append(f"{path}: original .eml is not reachable")
                self._set(errors=errors, error_samples=list(samples))
                continue
            try:
                rec = parse_eml_file(path)
                rec["path"] = abs_path
                rec["filename"] = path.name
                rec["folder"] = folder or rel_folder(root, path)
                with self.store.lock:
                    self.store.upsert_email(rec)
                updated += 1
                if updated % 25 == 0:
                    with self.store.lock:
                        self.store.commit()
                self._set(updated=updated)
            except (ParseError, OSError, ValueError) as exc:
                errors += 1
                if len(samples) < 8:
                    samples.append(f"{path}: {exc}")
                self._set(errors=errors, error_samples=list(samples))
        with self.store.lock:
            self.store.commit()
            self.store.set_meta("last_index", str(int(time.time())))
        self._set(
            running=False,
            phase="done",
            mode="repair_empty",
            updated=updated,
            skipped=skipped,
            errors=errors,
            removed=0,
            current="",
            finished_at=int(time.time()),
        )
