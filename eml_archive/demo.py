"""Generate a handful of sample .eml files so the viewer can be tried without a network drive."""

from __future__ import annotations

import email.policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def write_demo_archive(root: Path) -> Path:
    root = Path(root)
    (root / "Invoices" / "2024").mkdir(parents=True, exist_ok=True)
    (root / "Projects" / "Lomax").mkdir(parents=True, exist_ok=True)
    (root / "Personal").mkdir(parents=True, exist_ok=True)

    _write(
        root / "Invoices" / "2024" / "invoice-1042.eml",
        from_addr=("Acme Billing", "billing@acme.example"),
        to_addrs=[("Valby Lyntryk", "valby@valbylyntryk.dk")],
        subject="Invoice 1042 — paper delivery",
        date_tuple=(2024, 3, 12, 9, 15, 0),
        text="Please find invoice 1042 attached.\n\nTotal: 4.250,00 kr.\nDue: 26 March 2024.\n",
        html=(
            "<p>Please find invoice <b>1042</b> attached.</p>"
            "<p>Total: <strong>4.250,00 kr.</strong><br>Due: 26 March 2024.</p>"
        ),
        attachments=[("invoice-1042.txt", "text/plain", b"Invoice 1042\nTotal 4250 DKK\n")],
    )
    _write(
        root / "Projects" / "Lomax" / "bonus-question.eml",
        from_addr=("Anders", "anders@lomax.example"),
        to_addrs=[("Valby", "valby@valbylyntryk.dk")],
        cc_addrs=[("Shop", "shop@example.com")],
        subject="Re: KvartalsBonus 75%",
        date_tuple=(2024, 11, 2, 14, 40, 0),
        text=(
            "Hej Valby\n\n"
            "The 75% sticker is on the Twincase this week. Should we order extra?\n\n"
            "Anders\n"
        ),
    )
    _write(
        root / "Personal" / "family-dinner.eml",
        from_addr=("Mette", "mette@example.com"),
        to_addrs=[("Valby", "valby@valbylyntryk.dk")],
        subject="Sunday dinner — bring øl?",
        date_tuple=(2023, 6, 18, 18, 5, 0),
        text="We eat at 18:00. Can you bring øl and a salad?\n\nMette\n",
    )
    _write(
        root / "Projects" / "Lomax" / "html-only.eml",
        from_addr=("Newsletter", "news@shop.example"),
        to_addrs=[("Valby Lyntryk", "valby@valbylyntryk.dk")],
        subject="New catalogue is online",
        date_tuple=(2025, 1, 8, 8, 0, 0),
        html=(
            "<div style='font-family:sans-serif'>"
            "<h2>January catalogue</h2>"
            "<p>Browse the new <a href='https://example.com/cat'>catalogue</a>.</p>"
            "<p><img src='https://example.com/tracker.gif' alt='pixel'></p>"
            "<script>alert('xss')</script>"
            "</div>"
        ),
    )
    _write(
        root / "Sent" / "invoice-reply.eml",
        from_addr=("Valby Lyntryk", "post@valbylyntryk.dk"),
        to_addrs=[("Acme Billing", "billing@acme.example")],
        subject="Re: Invoice 1042 — paper delivery",
        date_tuple=(2024, 3, 12, 11, 20, 0),
        text=(
            "Tak for fakturaen. Vi betaler inden forfald.\n\n"
            "Venlig hilsen\nValby Lyntryk\n"
        ),
    )
    _write(
        root / "readme-note.eml",
        from_addr=("Archive Viewer", "archive@localhost"),
        to_addrs=[("You", "you@localhost")],
        subject="These files stay on the drive",
        date_tuple=(2026, 9, 21, 11, 0, 0),
        text=(
            "This viewer only reads .eml files where they are.\n"
            "Tags, stars and search live in a local database — nothing is moved.\n"
            "Mail from @valbylyntryk.dk is Sent Mail. Mail to that domain from anyone else is Received Mail.\n"
        ),
    )
    _write(
        root / "Projects" / "Lomax" / "outlook-html.eml",
        from_addr=("Newsletter", "news@shop.example"),
        to_addrs=[("Valby Lyntryk", "valby@valbylyntryk.dk")],
        subject="Please confirm the paper order",
        date_tuple=(2024, 4, 2, 10, 0, 0),
        html=(
            "<!--[if !mso]><!-->"
            "<div><p>Please confirm the paper order today.</p>"
            "<p>Total still 4.250,00 kr if we send this week.</p></div>"
            "<!--<![endif]-->"
            "<!--[if mso]><p>&nbsp;</p><![endif]-->"
        ),
    )
    return root


def _write(
    path: Path,
    *,
    from_addr: tuple[str, str],
    to_addrs: list[tuple[str, str]],
    subject: str,
    date_tuple: tuple[int, int, int, int, int, int],
    text: str | None = None,
    html: str | None = None,
    cc_addrs: list[tuple[str, str]] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = f"{from_addr[0]} <{from_addr[1]}>"
    msg["To"] = ", ".join(f"{n} <{a}>" for n, a in to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(f"{n} <{a}>" for n, a in cc_addrs)
    msg["Subject"] = subject
    import datetime as dt

    when = dt.datetime(*date_tuple, tzinfo=dt.timezone.utc)
    msg["Date"] = formatdate(when.timestamp(), localtime=False)
    msg["Message-ID"] = make_msgid(domain="archive.example")

    if html and text:
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    elif html:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(text or "")

    for filename, ctype, payload in attachments or []:
        main, _, sub = ctype.partition("/")
        msg.add_attachment(payload, maintype=main or "application", subtype=sub or "octet-stream", filename=filename)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes(policy=email.policy.SMTP))
