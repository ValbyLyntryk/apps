"""Sent vs received using Valby Lyntryk's own domain. Files are not moved."""

from __future__ import annotations

import re
from typing import Any

OWN_DOMAIN = "valbylyntryk.dk"


def _domain_address_re(domain: str) -> re.Pattern[str]:
    d = re.escape(domain.lower())
    # Real mailbox only: local-part@domain, not a bare "@domain" mention.
    return re.compile(
        rf"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@{d}(?![A-Za-z0-9.\-])",
        re.IGNORECASE,
    )


def _contains_domain(text: str, domain: str = OWN_DOMAIN) -> bool:
    if not text:
        return False
    return bool(_domain_address_re(domain).search(str(text)))


def from_own_domain(
    sender_email: str = "",
    sender: str = "",
    domain: str = OWN_DOMAIN,
) -> bool:
    return _contains_domain(sender_email, domain) or _contains_domain(sender, domain)


def to_own_domain(
    recipient_emails: str = "",
    recipients: str = "",
    cc: str = "",
    domain: str = OWN_DOMAIN,
) -> bool:
    return (
        _contains_domain(recipient_emails, domain)
        or _contains_domain(recipients, domain)
        or _contains_domain(cc, domain)
    )


def classify_mailbox(
    sender_email: str = "",
    sender: str = "",
    recipient_emails: str = "",
    recipients: str = "",
    cc: str = "",
    domain: str = OWN_DOMAIN,
) -> str:
    """Return 'sent', 'received', or '' (neither).

    Sent: From is @valbylyntryk.dk (including mail to ourselves).
    Received: From is someone else and To/Cc includes @valbylyntryk.dk.
    """
    if from_own_domain(sender_email, sender, domain):
        return "sent"
    if to_own_domain(recipient_emails, recipients, cc, domain):
        return "received"
    return ""


def classify_record(rec: dict[str, Any], domain: str = OWN_DOMAIN) -> str:
    return classify_mailbox(
        sender_email=rec.get("sender_email") or "",
        sender=rec.get("sender") or "",
        recipient_emails=rec.get("recipient_emails") or "",
        recipients=rec.get("recipients") or "",
        cc=rec.get("cc") or "",
        domain=domain,
    )


def _sql_col_has_domain(expr: str, domain: str) -> str:
    d = domain.lower().replace("'", "")
    # SQLite GLOB: require an alnum local-part char before @, then a complete
    # host (not a bare "@domain" mention and not @domain.evil.com).
    clauses = [f"lower({expr}) GLOB '*[0-9a-z]@{d}'"]
    for suf in (" ", ",", ";", ">", ")", "\t"):
        clauses.append(f"lower({expr}) GLOB '*[0-9a-z]@{d}{suf}*'")
    return "(" + " OR ".join(clauses) + ")"


def sql_from_own(alias: str = "e", domain: str = OWN_DOMAIN) -> str:
    return (
        f"({_sql_col_has_domain(f'{alias}.sender_email', domain)}"
        f" OR {_sql_col_has_domain(f'{alias}.sender', domain)})"
    )


def sql_to_own(alias: str = "e", domain: str = OWN_DOMAIN) -> str:
    parts = [
        _sql_col_has_domain(f"{alias}.{col}", domain)
        for col in ("recipient_emails", "recipients", "cc")
    ]
    return "(" + " OR ".join(parts) + ")"


def sql_mailbox(kind: str, alias: str = "e", domain: str = OWN_DOMAIN) -> str:
    kind = (kind or "").strip().lower()
    if kind in {"sent", "sent-mail", "sent_mail"}:
        return sql_from_own(alias, domain)
    if kind in {"received", "received-mail", "received_mail", "inbox"}:
        return f"(NOT {sql_from_own(alias, domain)} AND {sql_to_own(alias, domain)})"
    return "1=1"
