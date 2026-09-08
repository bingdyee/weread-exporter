"""Export a cached book without starting a browser."""

from __future__ import annotations

import hashlib
import html
import io
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import epub
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .content import asset_path, html_to_markdown, html_to_text, normalize_html
from .models import Book, Format
from .storage import atomic_write, safe_name


def _copy_assets(book: Book, source: Path, output: Path) -> None:
    for chapter in book.chapters:
        for img in BeautifulSoup(chapter.html, "lxml").find_all("img"):
            reference = str(img.get("src", ""))
            path = asset_path(source, reference)
            if path is None:
                raise ValueError(f"插图缓存不存在或路径无效：{reference}")
            target = output / reference
            if path != target.resolve():
                atomic_write(target, path.read_bytes())


def _epub(book: Book, source: Path) -> bytes:
    result = epub.EpubBook()
    result.set_identifier(f"weread:{book.id}")
    result.set_title(book.title)
    result.set_language("zh-CN")
    if book.author:
        result.add_author(book.author)
    if book.source_url:
        result.add_metadata("DC", "source", book.source_url)
    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=b"body{line-height:1.8} img{max-width:100%;height:auto} table{width:100%}",
    )
    result.add_item(css)
    images: set[str] = set()
    sections = []
    media = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
    for chapter in book.chapters:
        body = normalize_html(chapter.html)
        for img in BeautifulSoup(body, "lxml").find_all("img"):
            reference = str(img.get("src", ""))
            path = asset_path(source, reference)
            if path is None:
                raise ValueError(f"插图缓存不存在或路径无效：{reference}")
            if reference not in images:
                result.add_item(
                    epub.EpubItem(
                        uid=f"image_{len(images)}",
                        file_name=reference,
                        media_type=media[path.suffix],
                        content=path.read_bytes(),
                    )
                )
                images.add(reference)
        section = epub.EpubHtml(
            uid=f"chapter_{chapter.uid}",
            title=chapter.title,
            file_name=f"chapter_{chapter.uid}.xhtml",
            lang="zh-CN",
        )
        section.content = f"<h1>{html.escape(chapter.title)}</h1>{body}"
        section.add_item(css)
        result.add_item(section)
        sections.append(section)
    result.toc = sections
    result.spine = ["nav", *sections]
    result.add_item(epub.EpubNcx())
    result.add_item(epub.EpubNav())
    stream = io.BytesIO()
    epub.write_epub(stream, result, {"raise_exceptions": True})
    return stream.getvalue()


def _pdf_font(font: Path | None) -> str:
    candidates = (
        [font]
        if font
        else [
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"),
        ]
    )
    for path in candidates:
        if path and path.is_file():
            name = "WeRead" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
                pdfmetrics.registerFontFamily(
                    name, normal=name, bold=name, italic=name, boldItalic=name
                )
            return name
    if font:
        raise ValueError(f"PDF 字体不存在：{font}")
    # PDF standard CJK font; readers supply it when no embeddable system font is available.
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        pdfmetrics.registerFontFamily(
            "STSong-Light",
            normal="STSong-Light",
            bold="STSong-Light",
            italic="STSong-Light",
            boldItalic="STSong-Light",
        )
    return "STSong-Light"


def _inline(node: Tag | NavigableString) -> str:
    if isinstance(node, NavigableString):
        return html.escape(str(node))
    content = "".join(_inline(child) for child in node.children)
    if node.name == "br":
        return "<br/>"
    if node.name == "img":
        return html.escape(f"[图片：{node.get('alt') or '插图'}]")
    tag = {"strong": "b", "em": "i"}.get(node.name, node.name)
    if tag in {"b", "i", "u", "sup", "sub"}:
        return f"<{tag}>{content}</{tag}>"
    return content


def _pdf(book: Book, source: Path, font: Path | None) -> bytes:
    font_name = _pdf_font(font)
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
        style.wordWrap = "CJK"
    body = ParagraphStyle(
        "ReaderBody",
        parent=styles["BodyText"],
        fontSize=10.5,
        leading=18,
        spaceAfter=8,
        splitLongWords=True,
    )
    title = ParagraphStyle(
        "ReaderTitle",
        parent=styles["Title"],
        fontSize=25,
        leading=36,
        alignment=TA_CENTER,
        spaceAfter=18,
    )
    stream = io.BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=(210 * mm, 297 * mm),
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=book.title,
        author=book.author,
    )
    story = [Spacer(1, 35 * mm), Paragraph(html.escape(book.title), title)]
    if book.author:
        story.append(Paragraph(html.escape(book.author), styles["Normal"]))
    story.extend([Spacer(1, 15 * mm), Paragraph("目录", styles["Heading1"])])
    for chapter in book.chapters:
        story.append(Paragraph(html.escape(chapter.title), body))

    def flow(node: Tag | NavigableString) -> list:
        if isinstance(node, NavigableString):
            return [Paragraph(html.escape(str(node)), body)] if str(node).strip() else []
        if node.name == "img":
            reference = str(node.get("src", ""))
            path = asset_path(source, reference)
            if path is None:
                raise ValueError(f"插图缓存不存在或路径无效：{reference}")
            # Pillow normalizes WebP/GIF and prevents unsupported PDF image encodings.
            from PIL import Image as PILImage

            with PILImage.open(path) as original:
                buffer = io.BytesIO()
                original.convert("RGB").save(buffer, format="PNG")
            buffer.seek(0)
            image = Image(buffer)
            scale = min(1, (document.width - 12) / image.imageWidth, 180 * mm / image.imageHeight)
            image.drawWidth = image.imageWidth * scale
            image.drawHeight = image.imageHeight * scale
            return [image, Spacer(1, 8)]
        if node.name == "hr":
            return [HRFlowable(width="100%", color=colors.HexColor("#dddddd")), Spacer(1, 8)]
        if node.name == "table":
            rows = [
                [
                    Paragraph(_inline(cell), body)
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                for row in node.find_all("tr")
            ]
            rows = [row for row in rows if row]
            if not rows:
                return []
            columns = max(map(len, rows))
            rows = [row + [""] * (columns - len(row)) for row in rows]
            table = Table(rows, colWidths=[(document.width - 12) / columns] * columns)
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            return [table, Spacer(1, 8)]
        if node.name in {"ul", "ol"}:
            result = []
            for i, child in enumerate(node.find_all("li", recursive=False), 1):
                prefix = f"{i}. " if node.name == "ol" else "• "
                result.append(Paragraph(html.escape(prefix) + _inline(child), body))
            return result
        if node.name in {"div", "section", "body", "figure"} or node.find("img"):
            return [item for child in node.children for item in flow(child)]
        style = (
            styles.get(f"Heading{node.name[1]}", body)
            if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            else body
        )
        content = _inline(node)
        if node.name == "pre":
            content = content.replace("\n", "<br/>")
        return [Paragraph(content, style)] if content.strip() else []

    for chapter in book.chapters:
        story.extend([PageBreak(), Paragraph(html.escape(chapter.title), styles["Heading1"])])
        soup = BeautifulSoup(normalize_html(chapter.html), "lxml")
        for node in (soup.body or soup).children:
            story.extend(flow(node))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(105 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def export_book(
    book: Book,
    source: Path,
    output: Path,
    formats: list[Format],
    *,
    pdf_font: Path | None = None,
) -> list[Path]:
    if not book.chapters:
        raise ValueError("没有可导出的章节")
    if book.selected_chapter_uids and book.selected_chapter_uids != [c.uid for c in book.chapters]:
        raise ValueError("所选章节尚未全部下载完成，请先用 download --cache 续传")
    output.mkdir(parents=True, exist_ok=True)
    files = []
    for format_name in dict.fromkeys(formats):
        if format_name in {"epub", "pdf"}:
            data = _epub(book, source) if format_name == "epub" else _pdf(book, source, pdf_font)
            target = output / f"{safe_name(book.title)}.{format_name}"
            atomic_write(target, data)
            files.append(target)
        elif format_name in {"markdown", "txt"}:
            extension = "md" if format_name == "markdown" else "txt"
            if format_name == "markdown":
                _copy_assets(book, source, output)
            contents = []
            for chapter in book.chapters:
                if format_name == "markdown":
                    heading = html_to_markdown(f"<h1>{html.escape(chapter.title)}</h1>")
                    content = f"{heading}\n\n{html_to_markdown(chapter.html)}\n"
                else:
                    content = f"{chapter.title}\n\n{html_to_text(chapter.html)}\n"
                contents.append(content)
            target = output / f"{safe_name(book.title)}.{extension}"
            heading = (
                html_to_markdown(f"<h1>{html.escape(book.title)}</h1>")
                if format_name == "markdown"
                else book.title
            )
            atomic_write(target, heading + "\n\n" + "\n".join(contents))
            files.append(target)
        else:
            raise ValueError(f"不支持的格式：{format_name}")
    return files
