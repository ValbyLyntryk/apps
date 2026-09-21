# apps

## Email archive viewer

Browse thousands of archived `.eml` files **where they sit** on a network drive.
The program never moves or copies the mail. A local search index (plus optional
tags, stars, and notes) lives on your computer.

Needs Python 3 only. No extra packages.

```bash
# Point it at the folder of .eml files, then open http://127.0.0.1:8765/
python3 email_archive.py --archive "/path/to/network/drive/emails"

# Try a built-in sample archive first
python3 email_archive.py --demo
```

On Windows a mapped drive works the same way, for example `--archive "Z:\MailArchive"`.
UNC paths (`\\server\share\emails`) are fine too.

What you can do:

- Search subject, body, sender, folder (`from:alice after:2020-01-01 has:attachment`)
- Sort by date, sender, subject, size, or folder
- Filter by the original folders on disk, year, unread, starred, or tags
- Read HTML or plain-text mail and download attachments from the original file
- Tag / star / note messages without touching the `.eml` files

The search index can live next to the mail. On Windows:

```bat
EmailArchive.exe --archive "Y:\Mails" --db "Y:\Mails"
```

That writes `Y:\Mails\archive.db`. You can also click **Index location…** in the app and choose `Y:\Mails`. Tick **Copy the existing index** so the hours you already spent indexing are copied instead of rebuilt.

A small pointer is saved in `%USERPROFILE%\.email-archive\index-path.txt` so the next launch uses that drive. The `.eml` files themselves still stay where they are.

Re-run indexing after new mail is added; unchanged files are skipped.

### Portable Windows `.exe`

On a Windows PC that has Python 3 installed once, double-click `build_exe.bat`
(or run `python build_exe.py`). That produces `dist\EmailArchive.exe`. Copy
that single file to any other Windows machine — Python is **not** required
there. Point it at the network drive in the app, or:

```bat
EmailArchive.exe --archive "Z:\MailArchive"
EmailArchive.exe --demo
```

GitHub Actions also builds the `.exe` (workflow **Email archive exe**). Open
**Actions**, run it, and download the artifact.

Keep the black console window open while you use the app — closing it quits
the viewer. If that window flashes and disappears, look for
`EmailArchive-crash.log` next to the `.exe` or in `%USERPROFILE%\.email-archive\`.

```bash
python3 -m unittest test_email_archive.py
```

---

## Lomax bonus checker

If you just want email alerts when a new 75% or 100% Lomax bonus appears,
start here:

**[Dummy setup guide](GUIDE.md)**

That guide uses an extra email address and either GitHub (computer can be
off) or a watch loop on your own machine.

---

`lomax_bonus.py` scrapes the Lomax search listing and ranks products by their
KvartalsBonus sticker. Everyday items show 5%. This tool highlights the
campaign bonuses above that.

Needs Python 3 only. No extra packages.

```bash
# Full catalog, show 25%+ bonuses, remember the run
python3 lomax_bonus.py

# Test that email settings work
python3 lomax_bonus.py --send-test-email

# Hourly loop (reads lomax-bonus.env if present)
python3 lomax_bonus.py --watch
```

Copy `lomax-bonus.env.example` to `lomax-bonus.env` and fill in the extra
email plus an App Password. Never commit `lomax-bonus.env`.

```bash
python3 -m unittest test_lomax_bonus.py
```
