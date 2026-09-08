from unittest.mock import AsyncMock

import pytest

from weread_exporter import downloader
from weread_exporter.browser import ReaderError
from weread_exporter.models import Book, BookConfig, ChapterInfo, Config
from weread_exporter.storage import read_book


async def test_failed_chapter_resumes_from_cache(tmp_path, sample_book, monkeypatch):
    context = AsyncMock()
    page = context.new_page.return_value
    config = Config()
    config.weread.output = tmp_path
    config.weread.delay = 0
    config.weread.retries = 1
    config.weread.enable_cache = True

    async def open_book(reader, book_id, headless):
        reader.chapter_infos = [
            ChapterInfo(**chapter.model_dump(include=set(ChapterInfo.model_fields)))
            for chapter in sample_book.chapters
        ]
        return Book(id=book_id, title=sample_book.title, chapters=[])

    calls = []

    async def fail_second(reader, info, directory):
        calls.append(info.uid)
        if info.uid == 20:
            raise ReaderError("transient failure")
        return sample_book.chapters[0]

    monkeypatch.setattr(downloader, "open_book", open_book)
    monkeypatch.setattr(downloader, "download_chapter", fail_second)
    with pytest.raises(ReaderError, match="续传"):
        await downloader.download_book(context, BookConfig(id=sample_book.id), config)
    assert calls == [10, 20, 20]
    page.reload.assert_awaited_once()
    page.close.assert_awaited_once()
    directory = next(tmp_path.iterdir())
    partial, _ = read_book(directory)
    assert [chapter.uid for chapter in partial.chapters] == [10]
    assert partial.selected_chapter_uids == [10, 20]
    assert {
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    } == {
        "book.json",
        ".cache/10.json",
    }

    calls.clear()

    async def succeed(reader, info, directory):
        calls.append(info.uid)
        return sample_book.chapters[1]

    monkeypatch.setattr(downloader, "download_chapter", succeed)
    book, _ = await downloader.download_book(context, BookConfig(id=sample_book.id), config)
    assert calls == [20]
    assert [chapter.uid for chapter in book.chapters] == [10, 20]
    assert {
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    } == {
        "book.json",
        ".cache/10.json",
        ".cache/20.json",
    }
