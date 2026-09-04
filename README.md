# apps

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
