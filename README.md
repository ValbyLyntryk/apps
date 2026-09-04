# apps

## Lomax bonus checker

`lomax_bonus.py` scrapes the Lomax search listing and ranks products by their
KvartalsBonus sticker. Everyday items show 5%. This tool highlights the
campaign bonuses above that.

Needs Python 3 only. No extra packages.

```bash
# Full catalog, show 25%+ bonuses, remember the run
python3 lomax_bonus.py

# Only the very best stickers
python3 lomax_bonus.py --min-bonus 50
python3 lomax_bonus.py --min-bonus 100

# Quick sample
python3 lomax_bonus.py --pages 3 --no-save

# Export
python3 lomax_bonus.py --csv bonuses.csv --json bonuses.json
```

### Hourly watch and alerts

The first run is a silent baseline. After that, only **new or upgraded 75% /
100%** stickers trigger an alert.

Leave it running on a machine that stays on:

```bash
# Phone push via ntfy.sh — pick a hard-to-guess topic, then subscribe
# in the ntfy app or at https://ntfy.sh/your-private-topic
python3 lomax_bonus.py --watch --ntfy-topic your-private-topic
```

Or in the background:

```bash
nohup python3 lomax_bonus.py --watch --ntfy-topic your-private-topic > lomax-bonus.log 2>&1 &
```

Other alert channels:

```bash
python3 lomax_bonus.py --watch --desktop
python3 lomax_bonus.py --watch --webhook-url https://example.com/hook
python3 lomax_bonus.py --watch --github-issue
```

Email needs `LOMAX_SMTP_HOST`, `LOMAX_SMTP_USER`, `LOMAX_SMTP_PASSWORD`,
and `--email you@example.com`.

### GitHub hourly job

After this workflow is on `main`, GitHub Actions checks once an hour and
opens an issue when something new hits 75% or 100%. You get the normal
GitHub notification / email if you watch the repo.

Optional repository secrets for extra channels:

- `LOMAX_NTFY_TOPIC`
- `LOMAX_WEBHOOK_URL`
- `LOMAX_ALERT_EMAIL` plus the `LOMAX_SMTP_*` secrets

Run it by hand from the Actions tab with **workflow_dispatch** to confirm
it works, then leave the hourly schedule on.

Each full run writes `lomax-bonus-latest.json`. The next run compares against
that file and prints `NEW` / `CHG` / `GONE` so you can see what moved.

```bash
python3 -m unittest test_lomax_bonus.py
```
