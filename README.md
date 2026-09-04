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

Each full run writes `lomax-bonus-latest.json`. The next run compares against
that file and prints `NEW` / `CHG` / `GONE` so you can see what moved.

```bash
python3 -m unittest test_lomax_bonus.py
```
