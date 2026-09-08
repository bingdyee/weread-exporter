"""Normalize chapter HTML into a safe, portable subset shared by all exporters."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify

ALLOWED = {
    "p",
    "div",
    "span",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "s",
    "sup",
    "sub",
    "blockquote",
    "pre",
    "code",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "a",
    "img",
    "figure",
    "figcaption",
}
ASSET_PATTERN = re.compile(r"assets/[0-9a-f]{64}\.(?:png|jpg|gif|webp)")


def asset_path(directory: Path, reference: str) -> Path | None:
    if not ASSET_PATTERN.fullmatch(reference):
        return None
    root = directory.resolve()
    path = (root / reference).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def normalize_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select(
        "script, style, iframe, object, embed, form, input, button, svg, canvas"
    ):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    body = soup.body or soup
    for tag in list(body.find_all(True)):
        if tag.name not in ALLOWED:
            tag.unwrap()
            continue
        attrs = dict(tag.attrs)
        tag.attrs = {}
        if tag.name == "a":
            href = str(attrs.get("href", ""))
            if href.startswith(("https://", "http://", "#")):
                tag["href"] = href
        elif tag.name == "img":
            # WeRead keeps a transparent placeholder in src until the image scrolls into view.
            # Resolve the real URL before removing lazy-loading attributes.
            tag["src"] = str(attrs.get("data-src") or attrs.get("src") or "").strip()
            tag["alt"] = str(attrs.get("alt", ""))
        elif tag.name in {"td", "th"}:
            for name in ("colspan", "rowspan"):
                if str(attrs.get(name, "")).isdigit():
                    tag[name] = str(attrs[name])
    return body.decode_contents().strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(normalize_html(html), "lxml")
    for tag in soup.find_all("img"):
        tag.replace_with(f"[图片：{tag.get('alt') or '插图'}]")
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(
        ["p", "div", "li", "blockquote", "tr", "pre", "h1", "h2", "h3", "h4", "h5", "h6"]
    ):
        tag.insert_after("\n\n")
    text = (soup.body or soup).get_text().replace("\xa0", " ")
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()


def html_to_markdown(html: str) -> str:
    return markdownify(normalize_html(html), heading_style="ATX", bullets="-").strip()
