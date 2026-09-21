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
    "small",
    "span",
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
    "u",
    "ul",
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
    r"^\s*(?:(?:color|background(?:-color)?|font(?:-size|-family|-weight|-style)?|"
    r"text-(?:align|decoration)|vertical-align|white-space|width|height|margin|"
    r"padding|border(?:-collapse)?|line-height)\s*:\s*[^;{}]+;?\s*)+$",
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
        if tag in {"script", "style", "iframe", "object", "embed", "link", "meta", "base", "form", "input", "button", "textarea", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag not in ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        for name, value in attrs:
            if not name:
                continue
            name = name.lower()
            if name.startswith("on"):
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
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{attr_s}>")
        else:
            self.out.append(f"<{tag}{attr_s}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "link", "meta", "base", "form", "input", "button", "textarea", "svg"}:
            if self.skip_depth:
                self.skip_depth -= 1
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


def sanitize_html(raw: str, *, allow_remote: bool, cid_prefix: str) -> tuple[str, int]:
    parser = _Sanitizer(allow_remote=allow_remote, cid_prefix=cid_prefix)
    try:
        parser.feed(raw or "")
        parser.close()
    except Exception:
        return html.escape(raw or ""), 0
    return "".join(parser.out), parser.blocked_remote_images


def wrap_document(inner: str, *, title: str = "Message") -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)}</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #fff; color: #111; }}
  body {{ font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; padding: 16px 20px 32px; }}
  img {{ max-width: 100%; height: auto; }}
  a {{ color: #1d4ed8; }}
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
