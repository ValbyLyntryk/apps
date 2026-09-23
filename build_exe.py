#!/usr/bin/env python3
"""Freeze the email archive viewer into a portable executable.

Run this on Windows to get dist\\EmailArchive.exe (no Python required on the
PC that will use it). The same script also builds a Linux/macOS binary.

    python build_exe.py
    python build_exe.py --onedir
    python build_exe.py --windowed
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".build-venv"
REQ_FILE = ROOT / "requirements-build.txt"
ENTRY = ROOT / "email_archive.py"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_builder(python: Path, *, reuse_current: bool) -> Path:
    if not reuse_current:
        try:
            print(f"Creating build virtualenv at {VENV_DIR}", flush=True)
            if VENV_DIR.exists():
                shutil.rmtree(VENV_DIR)
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
            py = _venv_python(VENV_DIR)
            probe = subprocess.run(
                [str(py), "-m", "pip", "--version"],
                capture_output=True,
                text=True,
            )
            if probe.returncode != 0:
                raise RuntimeError(probe.stderr.strip() or "venv has no pip")
            subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
            subprocess.check_call([str(py), "-m", "pip", "install", "-q", "-r", str(REQ_FILE)])
            return py
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            detail = (
                "python3-venv/ensurepip not available"
                if isinstance(exc, SystemExit)
                else (str(exc) or type(exc).__name__)
            )
            print(
                f"Virtualenv build skipped ({detail}). Installing PyInstaller into the current Python.",
                flush=True,
            )
            shutil.rmtree(VENV_DIR, ignore_errors=True)
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "-r", str(REQ_FILE)])
    return python


def _output_name(name: str) -> str:
    if os.name == "nt" and not name.lower().endswith(".exe"):
        return name + ".exe"
    return name


def build(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a portable Email Archive Viewer executable")
    parser.add_argument("--name", default="EmailArchive", help="Output name (default EmailArchive)")
    parser.add_argument("--onedir", action="store_true", help="Folder of files instead of one executable")
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Windows GUI app (no console). Default keeps a console so you can see status.",
    )
    parser.add_argument("--noconsole", action="store_true", help="Alias for --windowed")
    parser.add_argument("--use-current-python", action="store_true", help="Install PyInstaller into this Python")
    parser.add_argument("--skip-smoke", action="store_true", help="Do not run --help on the built file")
    args = parser.parse_args(argv)

    if not ENTRY.is_file():
        print(f"Missing {ENTRY}", file=sys.stderr)
        return 2

    builder = _ensure_builder(Path(sys.executable), reuse_current=args.use_current_python)
    dist = ROOT / "dist"
    work = ROOT / "build" / "pyinstaller"
    sep = ";" if os.name == "nt" else ":"
    static = ROOT / "eml_archive" / "static"
    add_data = f"{static}{sep}eml_archive/static"

    cmd = [
        str(builder),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        args.name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--collect-submodules",
        "eml_archive",
        "--collect-data",
        "eml_archive",
        "--add-data",
        add_data,
        "--paths",
        str(ROOT),
    ]
    cmd.append("--onedir" if args.onedir else "--onefile")
    if args.windowed or args.noconsole:
        cmd.append("--windowed")
    else:
        cmd.append("--console")
    cmd.append(str(ENTRY))

    print("Running:", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(ROOT))

    if args.onedir:
        built = dist / args.name / _output_name(args.name)
    else:
        built = dist / _output_name(args.name)
    if not built.exists():
        print(f"Build finished but {built} was not found", file=sys.stderr)
        return 1

    if not args.skip_smoke:
        smoke = [str(built), "--help"]
        print("Smoke test:", " ".join(smoke), flush=True)
        subprocess.check_call(smoke)

    size = built.stat().st_size
    print(f"\nPortable app: {built} ({size:,} bytes)")
    if os.name == "nt":
        print("Copy that .exe wherever you want. Python does not need to be installed.")
        print("The search index still lives in %USERPROFILE%\\.email-archive\\")
    else:
        print("On Windows, run build_exe.bat or: python build_exe.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build())
    except subprocess.CalledProcessError as exc:
        print(f"Build failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
