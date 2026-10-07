# Dummy guide: email Spotify receipts to the bookkeeping inbox

Spotify keeps receipts on the account page. They do **not** give a public
invoice API, and they usually do **not** email every monthly charge.

This script logs in once in a browser on **your computer**, remembers that
login in a local file, then downloads new receipts and emails them (for
example to `faktura@valbylyntryk.dk`).

GitHub cannot do this for you. Spotify sign-in needs your screen.

You do **not** paste the Spotify password into this chat, a commit, or
`spotify-invoices.env`.

---

## Part 1. One-time setup on the PC that already uses Spotify

You need Python 3. On Windows, install it from https://www.python.org/downloads/
and tick **Add python.exe to PATH**.

1. Open Command Prompt or Terminal in this folder (`apps`).
2. Install the browser helper:

```bash
python3 -m pip install -r requirements-spotify.txt
python3 -m playwright install chromium
```

On Windows, `python3` may be `py`:

```bat
py -m pip install -r requirements-spotify.txt
py -m playwright install chromium
```

3. Copy the example settings:

```bash
cp spotify-invoices.env.example spotify-invoices.env
```

On Windows: copy `spotify-invoices.env.example` and rename the copy to
`spotify-invoices.env`.

4. Open `spotify-invoices.env` in a text editor.
5. Set `SPOTIFY_ALERT_EMAIL` to the inbox that should **receive** the PDFs
   (bookkeeping, often `faktura@valbylyntryk.dk`).
6. Fill in the Gmail/Outlook that **sends** the mail, plus a 16-character
   **App Password** — same idea as the Lomax guide.  
   If `lomax-bonus.env` already has working SMTP settings, you can leave the
   `SPOTIFY_SMTP_*` lines empty and the script will reuse those.
7. Save the file. Do not share it. Do not commit it.

---

## Part 2. Save the Spotify login (once)

```bash
python3 spotify_invoices.py --login
```

A Chromium window opens.

1. Log into the Spotify account that pays Premium.
2. Wait until you see the **account** page, not the login form.
3. Come back to the terminal and press **Enter**.

That writes `spotify-storage.json` in this folder. Treat it like a password.
If Spotify later asks you to log in again, repeat this step.

---

## Part 3. Send a test mail (no Spotify scrape)

```bash
python3 spotify_invoices.py --send-test-email
```

Check the bookkeeping inbox **and Spam**. If that mail arrived, sending works.

---

## Part 4. Fetch receipts and email the new ones

Watch it the first time:

```bash
python3 spotify_invoices.py --headed --dump
```

If that looks right, send for real:

```bash
python3 spotify_invoices.py
```

The first real run emails receipts from about the last 45 days, then remembers
the rest so you are not spammed with two years of PDFs. Later runs only email
**new** receipts.

Useful extras:

```bash
python3 spotify_invoices.py --dry-run --headed
python3 spotify_invoices.py --baseline
python3 spotify_invoices.py --all
```

`--baseline` remembers everything currently on the account and sends nothing.
`--all` ignores the 45-day window (still skips receipts already emailed).
`--dump` writes `spotify-receipts/debug-last.html` if the page layout changed.

---

## Once a month

Leave this on the same PC. After the first successful run:

```bash
python3 spotify_invoices.py
```

On Windows you can add that as a monthly Task Scheduler action. The PC needs
to be on, and `spotify-storage.json` must still be valid.

If a run says the login expired, do Part 2 again.

---

## What you get

A mail with PDF (or HTML) attachments named like
`spotify-receipt-2026-09-15-....pdf`.

These are Spotify **receipts**. They may not include CVR or a company address.
Spotify does not generate a custom Danish faktura.
