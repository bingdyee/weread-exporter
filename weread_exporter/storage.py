from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .models import IMAGE_SOURCE_VERSION, Book, Chapter, ChapterInfo


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100]
    if not value:
        return "untitled"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value):
        value = "_" + value
    return value


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8") if isinstance(content, str) else content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_book(book: Book, directory: Path) -> Path:
    target = directory / "book.json"
    atomic_write(target, book.model_dump_json(indent=2))
    return target


def read_book(path: Path) -> tuple[Book, Path]:
    path = path / "book.json" if path.is_dir() else path
    book = Book.model_validate_json(path.read_text(encoding="utf-8"))
    if not book.chapters:
        raise ValueError("书籍缓存没有章节")
    uids = [chapter.uid for chapter in book.chapters]
    if len(uids) != len(set(uids)):
        raise ValueError("书籍缓存包含重复章节 UID")
    return book, path.parent


def read_chapter(directory: Path, info: ChapterInfo) -> Chapter | None:
    try:
        chapter = Chapter.model_validate_json(
            (directory / ".cache" / f"{info.uid}.json").read_text(encoding="utf-8")
        )
        if chapter.image_source_version < IMAGE_SOURCE_VERSION and "<img" in chapter.html.lower():
            # Old caches may contain downloaded 1x1 placeholders with the real URL discarded.
            return None
        if chapter.uid == info.uid and chapter.title == info.title:
            return chapter.model_copy(update={"index": info.index, "level": info.level})
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def save_chapter(directory: Path, chapter: Chapter) -> None:
    atomic_write(directory / ".cache" / f"{chapter.uid}.json", chapter.model_dump_json(indent=2))


def select_chapters(chapters: list[ChapterInfo], names: list[str]) -> list[ChapterInfo]:
    """Select exact names, inclusive ranges and open ranges; keep catalogue order."""
    if not names:
        raise ValueError("章节选择为空；用 ... 选择全部章节")
    by_title: dict[str, list[int]] = {}
    for index, chapter in enumerate(chapters):
        by_title.setdefault(chapter.title, []).append(index)

    def position(name: str) -> int:
        matches = by_title.get(name, [])
        if not matches:
            raise ValueError(f"未找到章节：{name}")
        if len(matches) > 1:
            raise ValueError(f"章节名重复：{name}；请使用 ... 下载全部或交互式序号选择")
        return matches[0]

    selected: set[int] = set()
    for i, name in enumerate(names):
        if name != "...":
            selected.add(position(name))
            continue
        if (i > 0 and names[i - 1] == "...") or (i + 1 < len(names) and names[i + 1] == "..."):
            raise ValueError("不能连续使用 ...")
        start = position(names[i - 1]) if i else 0
        end = position(names[i + 1]) if i + 1 < len(names) else len(chapters) - 1
        if start > end:
            raise ValueError("章节范围起点不能晚于终点")
        selected.update(range(start, end + 1))
    if not selected:
        raise ValueError("没有可下载的章节")
    return [chapter for i, chapter in enumerate(chapters) if i in selected]
