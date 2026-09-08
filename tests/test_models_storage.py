import json

import pytest

from weread_exporter.models import ChapterInfo, read_config
from weread_exporter.storage import (
    read_book,
    read_chapter,
    safe_name,
    save_book,
    save_chapter,
    select_chapters,
)


def infos():
    return [ChapterInfo(uid=i + 10, index=i, title=name) for i, name in enumerate("ABCDE")]


@pytest.mark.parametrize(
    "names, expected",
    [
        (["..."], "ABCDE"),
        (["...", "C"], "ABC"),
        (["C", "..."], "CDE"),
        (["B", "...", "D"], "BCD"),
        (["D", "A", "A"], "AD"),
    ],
)
def test_selection(names, expected):
    assert "".join(c.title for c in select_chapters(infos(), names)) == expected


@pytest.mark.parametrize("names", [[], ["missing"], ["D", "...", "B"], ["...", "..."]])
def test_bad_selection(names):
    with pytest.raises(ValueError):
        select_chapters(infos(), names)


def test_duplicate_titles_require_unambiguous_selection():
    chapters = infos()
    chapters[1].title = "A"
    with pytest.raises(ValueError, match="重复"):
        select_chapters(chapters, ["A"])
    assert len(select_chapters(chapters, ["..."])) == 5


@pytest.mark.parametrize("combine", [True, False])
def test_legacy_config(tmp_path, combine):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "puppeteer": {"launch": {"executablePath": "/bin/chrome"}},
                "weread": {
                    "enableCache": True,
                    "books": [{"id": "abc123", "chapters": ["..."], "combine": combine}],
                },
            }
        )
    )
    config = read_config(path)
    assert config.browser.executable_path == "/bin/chrome"
    assert config.weread.enable_cache
    assert config.weread.formats == ["txt"]
    assert "combine" not in config.weread.books[0].model_dump()


def test_portable_cache(tmp_path, sample_book):
    manifest = save_book(sample_book, tmp_path)
    book, directory = read_book(manifest)
    assert book == sample_book
    assert directory == tmp_path
    chapter = book.chapters[0]
    info = ChapterInfo(**chapter.model_dump(include=set(ChapterInfo.model_fields)))
    save_chapter(tmp_path, chapter)
    assert read_chapter(tmp_path, info) == chapter
    (tmp_path / ".cache" / "10.json").write_text("{truncated")
    assert read_chapter(tmp_path, info) is None


@pytest.mark.parametrize("name", ["../", "..", "CON", "NUL.txt", "a/b\\c", "a\x00b"])
def test_safe_name(name):
    result = safe_name(name)
    assert result not in {"", ".", "..", "CON", "NUL.txt"}
    assert not any(char in result for char in "/\\\x00")
