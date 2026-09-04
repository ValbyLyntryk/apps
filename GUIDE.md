# Dummy guide: get an email when Lomax has a 75% or 100% bonus

This is the slow, click-by-click version.

When you are done, a robot looks at Lomax about once an hour. If a **new**
product gets a **75% or 100% bonus** sticker, your extra email inbox gets a
message. Products that already have that sticker today will **not** email you.
They are the starting point.

You do **not** need to leave this Cursor chat open.

---

## Pick one way to run the robot

**Path A — GitHub does it for you (best for most people)**  
Your computer can be off. GitHub wakes up every hour, checks Lomax, and emails
you. This needs the code on the `main` branch of your GitHub repo.

**Path B — Your own computer**  
Your computer (or a small always-on box) must stay on and stay online.

Do Path A if you are unsure.

---

## Part 1. Make the extra email allow a robot to send mail

Gmail will **not** accept your normal login password here. You need a special
16-character **App Password**. Outlook is similar.

### If the extra address is Gmail

1. Open that extra Gmail in a browser and sign in.
2. Turn on 2-Step Verification:  
   https://myaccount.google.com/signinoptions/two-step-verification
3. Create an App Password:  
   https://myaccount.google.com/apppasswords  
   If Google hides that page, 2-Step Verification is not on yet. Go back to
   step 2.
4. Name it something like `Lomax bonus`.
5. Copy the 16 characters. Spaces do not matter.  
   This is **not** your normal Gmail password. Keep it private.

### If the extra address is Outlook / Hotmail / Live

1. Sign in at https://account.microsoft.com/security
2. Turn on two-step verification if it is off.
3. Create an app password and copy it.
4. Later you will use server `smtp.office365.com` instead of `smtp.gmail.com`.

---

## Part 2. Get the code onto `main`

The hourly GitHub job only runs from the default branch, usually `main`.

1. Open the pull request: https://github.com/ValbyLyntryk/apps/pull/1
2. Merge it into `main` (the green **Merge pull request** button).
3. After that, the files `lomax_bonus.py`, `GUIDE.md`, and
   `.github/workflows/lomax-bonus-watch.yml` should be on `main`.

If you only want to try this on your laptop first, skip the merge and use
Path B with the files from this branch.

---

## Path A. GitHub sends the emails (recommended)

### A1. Put the email secrets in GitHub

Never paste the App Password into a chat, a commit, or the README.

1. Open the repo: https://github.com/ValbyLyntryk/apps
2. Click **Settings**.  
   If you do not see Settings, you are not logged in as the repo owner.
3. In the left menu: **Secrets and variables** → **Actions**.
4. Click **New repository secret** for each row below.

| Name | What to paste |
|---|---|
| `LOMAX_ALERT_EMAIL` | The extra email address, the one that should **receive** the alert |
| `LOMAX_SMTP_USER` | The same extra email address (the one that **sends**) |
| `LOMAX_SMTP_FROM` | The same extra email address again |
| `LOMAX_SMTP_PASSWORD` | The 16-character App Password from Part 1 |
| `LOMAX_SMTP_HOST` | `smtp.gmail.com` for Gmail, or `smtp.office365.com` for Outlook |
| `LOMAX_SMTP_PORT` | `587` |

You should now have 6 secrets.

### A2. Send a test email

1. Open the repo on GitHub.
2. Click the **Actions** tab.
3. If GitHub asks to enable Actions, say yes.
4. In the left list, click **Lomax bonus watch**.
5. Click **Run workflow**.
6. Tick **Send a test email now**.
7. Click the green **Run workflow** button.
8. Wait about a minute. Click the newest run. It should be green.
9. Check the extra inbox. Also check **Spam**.

If mail arrived, email is working.

### A3. Save today’s Lomax catalog (no spam)

1. **Actions** → **Lomax bonus watch** → **Run workflow** again.
2. This time leave **Send a test email now** **unticked**.
3. Run it.

This first real check is a baseline. You should **not** get a product email,
even if Lomax already has 75% or 100% items. That is on purpose.

### A4. Leave it alone

GitHub will now run the same job around the top of every hour. When a **new**
75% or 100% sticker appears, you get an email.

GitHub may also open an Issue on the repo. That is a second copy of the same
alert. You can ignore those issues, or close them.

If the repo goes quiet for a long time, GitHub sometimes pauses scheduled
jobs. Open the repo or run the workflow by hand once, and it starts again.

---

## Path B. Run it on your own computer

You need Python 3. On a Mac, Terminal already has `python3` most of the time.
On Windows, install Python from https://www.python.org/downloads/ and tick
**Add python.exe to PATH**.

1. Download or clone this repo so you have the `lomax_bonus.py` file.
2. In that folder, copy the example settings file:
   - Mac / Linux: `cp lomax-bonus.env.example lomax-bonus.env`
   - Windows: copy `lomax-bonus.env.example` and rename the copy to `lomax-bonus.env`
3. Open `lomax-bonus.env` in any text editor.
4. Replace the example address with your extra email, four times
   (`LOMAX_ALERT_EMAIL`, `LOMAX_SMTP_USER`, `LOMAX_SMTP_FROM`, and the comments
   if you want).
5. Paste the App Password after `LOMAX_SMTP_PASSWORD=`.
6. Save the file. Do not share this file. Do not commit it to GitHub.
7. Open Terminal (Mac) or Command Prompt (Windows) in that folder.
8. Send a test:

```bash
python3 lomax_bonus.py --send-test-email
```

9. Check the extra inbox and Spam.
10. Start the hourly loop:

```bash
python3 lomax_bonus.py --watch
```

Leave that window open. The first check is a baseline (no product email).
About an hour later it checks again, then again, and so on.

To run it in the background on a Mac or Linux machine that stays on:

```bash
nohup python3 lomax_bonus.py --watch > lomax-bonus.log 2>&1 &
```

---

## What a real alert looks like

Subject line like:

`Lomax bonus: 1× 100% just appeared`

The body has the product name, price, item number, and a link.

---

## If it does not work

**No App Password page in Google**  
2-Step Verification is off. Turn it on, wait a minute, try again.

**Test email command says SMTP is not set**  
You are in the wrong folder, or the file is still named
`lomax-bonus.env.example`. It must be exactly `lomax-bonus.env`.

**Gmail says username and password not accepted**  
You used the normal Gmail password. Use the 16-character App Password.
Create a new one if needed.

**Mail is missing**  
Wait two minutes. Check Spam. On GitHub, open the failed Action (red X) and
read the log.

**I got no product emails after a day**  
That can be correct. Alerts only fire when something **new** hits 75% or 100%.
Existing deals from the baseline stay quiet.

**I want a test product email without waiting**  
Path A: run the workflow with **Send a test email now**.  
Path B: `python3 lomax_bonus.py --send-test-email`

---

## Do not do these things

- Do not put the App Password in the chat, in `GUIDE.md`, or in GitHub files.
- Do not commit `lomax-bonus.env`.
- Do not use your main everyday password as `LOMAX_SMTP_PASSWORD`.
