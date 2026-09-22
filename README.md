# apps

## Email archive viewer

Browse thousands of archived `.eml` files **where they sit** on a network drive.
The program never moves or copies the mail. The search index (`archive.db`,
plus optional tags, stars, and notes) is stored **next to the program** — the
same folder as `EmailArchive.exe`. Put the .exe on the NAS and every PC that
launches it shares that index.

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

- Search subject, body, sender, folder (`from:alice mailbox:sent after:2020-01-01 has:attachment`)
- Sort by date, sender, subject, size, or folder
- Filter by the original folders on disk, year, starred, or tags
- **Sent Mail** is anything From `@valbylyntryk.dk` (including mail we sent to ourselves)
- **Received Mail** is anything From a different address and To/Cc/Bcc `@valbylyntryk.dk`
- Search `mailbox:sent` / `mailbox:received` — this uses From/To already in the index, so no reindex is required
- Read HTML or plain-text mail and download attachments from the original file
- Tag / star / note messages without touching the `.eml` files
- Right-click a message to show the `.eml` on disk or print it
- Drag the pane splitters to resize the sidebar, list, and reading view

The search index lives next to the program by default:

```bat
\\server\share\EmailArchive.exe
\\server\share\archive.db
```

Any PC that double-clicks that .exe uses the same `archive.db`. Override with `--db` if needed:

```bat
EmailArchive.exe --archive "Y:\Mails"
EmailArchive.exe --archive "Y:\Mails" --db "Y:\Mails"
```

Deleted or moved `.eml` files are dropped from the index on the next scan (including the scan that starts when you open the app). Unchanged files are skipped. There is no read/unread state — this is an archive.

Blank reading panes do **not** need a full reindex. Opening a message re-reads
the original `.eml` file. Restart the app (rebuild `EmailArchive.exe` if you
froze one) so this code actually runs.

**Repair blank bodies** only re-parses messages whose index row has no text
(the list preview is empty too). Minutes, not hours. Leave **Reindex** for a
full rebuild.

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
