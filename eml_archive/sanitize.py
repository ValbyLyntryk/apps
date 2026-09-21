"""Small HTML sanitizer for rendering archived messages in an iframe."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

ALLOWED_TAGS = {
    "a",
    "abbr",
    "article",
    "b",
    "blockquote",
    "br",
    "caption",
    "center",
    "cite",
    "code",
    "col",
    "colgroup",
    "dd",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "section",
    "small",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "tt",
    "u",
    "ul",
}

SKIP_TAGS = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "link",
    "meta",
    "base",
    "form",
    "input",
    "button",
    "textarea",
    "svg",
    "noscript",
    "template",
}

VOID_TAGS = {"br", "hr", "img", "col"}

ALLOWED_ATTR = {
    "abbr",
    "align",
    "alt",
    "border",
    "cellpadding",
    "cellspacing",
    "cite",
    "class",
    "colspan",
    "dir",
    "height",
    "href",
    "id",
    "rel",
    "rowspan",
    "src",
    "target",
    "title",
    "valign",
    "width",
}

ALLOWED_STYLE = re.compile(
    r"^\s*(?:(?:font(?:-size|-family|-weight|-style)?|"
    r"text-(?:align|decoration)|vertical-align|white-space|width|height|margin|"
    r"padding|border(?:-collapse)?)\s*:\s*[^;{}]+;?\s*)+$",
    re.I,
)

REMOTE_IMG_PLACEHOLDER = (
    "data:image/svg+xml;charset=utf-8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='24'>"
    "<rect fill='%23e5e7eb' width='120' height='24'/>"
    "<text x='8' y='16' font-size='11' fill='%236b7280'>remote image</text></svg>"
)


def _safe_url(value: str, allow_remote: bool) -> str | None:
    value = html.unescape(value.strip())
    parsed = urlparse(value)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https", "mailto"):
        if scheme in ("http", "https") and not allow_remote:
            return None
        return value
    if scheme in ("cid",):
        return value
    if value.startswith("#"):
        return value
    if scheme in ("",) and not value.startswith("//"):
        # relative — drop, we don't have the original site
        return None
    return None


class _Sanitizer(HTMLParser):
    def __init__(self, allow_remote: bool, cid_prefix: str) -> None:
        super().__init__(convert_charrefs=True)
        self.allow_remote = allow_remote
        self.cid_prefix = cid_prefix.rstrip("/")
        self.out: list[str] = []
        self.skip_depth = 0
        self.blocked_remote_images = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"html", "head", "body"}:
            # Unclosed <style> in the header must not blank the rest of the message.
            if tag == "body":
                self.skip_depth = 0
            return
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth and tag in ALLOWED_TAGS:
            self.skip_depth = 0
        if self.skip_depth:
            return
        if tag not in ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        for name, value in attrs:
            if not name:
                continue
            name = name.lower()
            if name.startswith("on") or name in {"color", "bgcolor"}:
                continue
            if name == "style" and value and ALLOWED_STYLE.match(value):
                safe_attrs.append(f'style="{html.escape(value, quote=True)}"')
                continue
            if name not in ALLOWED_ATTR:
                continue
            if value is None:
                continue
            if name in {"href", "src"}:
                if name == "src" and value.lower().startswith("cid:"):
                    cid = value[4:].strip().strip("<>")
                    value = f"{self.cid_prefix}/{quote(cid, safe='')}"
                elif name == "src":
                    url = _safe_url(value, self.allow_remote)
                    if url is None:
                        if tag == "img":
                            self.blocked_remote_images += 1
                            value = REMOTE_IMG_PLACEHOLDER
                        else:
                            continue
                    else:
                        value = url
                else:
                    url = _safe_url(value, allow_remote=True)
                    if url is None:
                        continue
                    value = url
                    if tag == "a":
                        safe_attrs.append('rel="noopener noreferrer"')
                        safe_attrs.append('target="_blank"')
            safe_attrs.append(f'{name}="{html.escape(str(value), quote=True)}"')
        attr_s = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.out.append(f"<{tag}{attr_s}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if tag in {"html", "head", "body"}:
            if tag == "head":
                self.skip_depth = 0
            return
        if self.skip_depth:
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self.skip_depth:
            return
        self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.skip_depth:
            return
        self.out.append(f"&#{name};")


def _unwrap_outlook_conditionals(raw: str) -> str:
    """Keep HTML inside Outlook ``<!--[if …]>`` blocks; drop only the wrappers.

    A previous version deleted everything from the first ``<!--[if`` to
    ``<![endif]-->``. Most Microsoft HTML mail puts the real body in
    ``<!--[if !mso]><!--> … <!--<![endif]-->``, so that ate the message
    and left a blank iframe while the list snippet (from the raw HTML)
    still showed the text.
    """

    def repl(match: re.Match[str]) -> str:
        return f" {match.group(1)} "

    raw = re.sub(
        r"(?is)<!--\[if[^\]]*\]>(?:<!-->)?(.*?)(?:<!--)?<!\[endif\]-->",
        repl,
        raw,
    )
    return raw


def _prestrip(raw: str) -> str:
    """Drop style/script/head so an unclosed tag cannot hide the whole message."""
    raw = _unwrap_outlook_conditionals(raw or "")
    raw = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", raw)
    raw = re.sub(
        r"(?is)<style\b[^>]*>.*?(?=<body\b|<(?:div|p|table|br|h[1-6]|center|span|font)\b)",
        " ",
        raw,
    )
    raw = re.sub(r"(?is)<head\b[^>]*>.*?</head>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    return raw


def sanitize_html(raw: str, *, allow_remote: bool, cid_prefix: str) -> tuple[str, int]:
    parser = _Sanitizer(allow_remote=allow_remote, cid_prefix=cid_prefix)
    try:
        parser.feed(_prestrip(raw or ""))
        parser.close()
    except Exception:
        from .parser import html_to_text

        return html.escape(html_to_text(raw or ""), quote=False).replace("\n", "<br>\n"), 0
    return "".join(parser.out), parser.blocked_remote_images


def _visible_len(html_fragment: str) -> int:
    from .parser import html_to_text

    text = html_to_text(html_fragment or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?i)remote image", " ", text)
    text = re.sub(r"\s+", "", text)
    return len(text)


def build_view_document(
    html_body: str,
    text_body: str,
    indexed_text: str = "",
    *,
    allow_remote: bool,
    cid_prefix: str,
    title: str = "Message",
) -> tuple[str, int]:
    """Build a readable HTML document, falling back if sanitizing ate the body."""
    from .parser import html_to_text

    blocked = 0
    inner = ""
    if html_body:
        inner, blocked = sanitize_html(html_body, allow_remote=allow_remote, cid_prefix=cid_prefix)
    vis = _visible_len(inner)
    orig = max(
        _visible_len(html_body or ""),
        len(re.sub(r"\s+", "", (text_body or "").replace("\xa0", " "))),
        len(re.sub(r"\s+", "", (indexed_text or "").replace("\xa0", " "))),
    )
    html_ok = vis >= 2 and not (orig >= 20 and vis < 8 and vis * 4 < orig)
    if html_ok:
        return wrap_document(inner, title=title), blocked

    fallback = (text_body or "").strip() or html_to_text(html_body or "").strip() or (indexed_text or "").strip()
    if fallback:
        return text_as_html(fallback), blocked
    if _visible_len(inner) >= 1:
        return wrap_document(inner, title=title), blocked
    return text_as_html(
        "The body of this message could not be extracted.\n\n"
        "A full reindex is not required. Opening a message re-reads the original .eml file.\n"
        "If the search snippet is also blank, click Repair blank bodies to refill empty index "
        "rows only (minutes, not hours)."
    ), blocked


def wrap_document(inner: str, *, title: str = "Message") -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)}</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #fff !important; color: #111 !important; }}
  body {{ font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; padding: 16px 20px 32px; color: #111 !important; background: #fff !important; }}
  body *:not(a) {{ color: #111 !important; }}
  img {{ max-width: 100%; height: auto; }}
  a {{ color: #1d4ed8 !important; }}
  pre, code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; }}
  table {{ border-collapse: collapse; max-width: 100%; }}
  blockquote {{ border-left: 3px solid #d1d5db; margin-left: 0; padding-left: 12px; color: #374151; }}
</style>
</head><body>{inner}</body></html>
"""


def text_as_html(text: str) -> str:
    escaped = html.escape(text or "", quote=False)
    escaped = escaped.replace("\n", "<br>\n")
    return wrap_document(f"<pre style='font-family: inherit; white-space: pre-wrap'>{escaped}</pre>")
