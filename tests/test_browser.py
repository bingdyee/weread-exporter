"""Local, deterministic browser tests. No WeRead account or network access required."""

import os

import pytest
from playwright.async_api import async_playwright

from weread_exporter.browser import ReaderError, ReaderPage
from weread_exporter.downloader import download_chapter, open_book
from weread_exporter.hooks import (
    DECRYPTION,
    INITIAL_STATE_REF,
    override_document,
    override_script,
)
from weread_exporter.models import ChapterInfo

pytestmark = pytest.mark.browser


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, channel=os.environ.get("WEREAD_TEST_BROWSER_CHANNEL")
        )
        page = await browser.new_page()
        page.set_default_timeout(2000)
        yield page
        await browser.close()


async def test_hooks_execute_and_keep_live_reader_state(page):
    script = """const crypto = {DES:{decrypt: (value) => value}};
        const fn = function(value, book, uid, index) {return crypto.DES.decrypt(value)};"""
    script = override_script("https://cdn.weread.qq.com/web/wrwebnjlogic/js/app.js", script)
    document = override_document(
        "https://weread.qq.com/web/reader/abc",
        '<script nonce="x">window.__INITIAL_STATE__={reader:{value:1}};</script>',
    )
    await page.set_content(document)
    await page.add_script_tag(content=script)
    assert await page.evaluate(f"window.{DECRYPTION}('正文', 'abc', 1, 0)") == "正文"
    await page.evaluate(
        "window.__INITIAL_STATE__.reader.value = 2; delete window.__INITIAL_STATE__"
    )
    assert await page.evaluate(f"window.{INITIAL_STATE_REF}.reader.value") == 2


async def test_all_sections_are_extracted(page, tmp_path):
    await page.evaluate(
        """({key, decrypt}) => {
            window[key] = {reader:{bookId:'abc',currentChapter:{chapterUid:10},
                chapterContentState:'DONE',currentSectionIdx:0,
                chapterContentHtml:[{value:'<p>第一段</p>'},{value:'<p>第二段</p>'}]}};
            window[decrypt] = (value, book, uid, index) => value + `<p>section=${index}</p>`;
        }""",
        {"key": INITIAL_STATE_REF, "decrypt": DECRYPTION},
    )
    chapter = await download_chapter(
        ReaderPage(page),
        ChapterInfo(uid=10, index=0, title="第一章"),
        tmp_path,
    )
    assert "第一段" in chapter.html and "第二段" in chapter.html
    assert "section=1" in chapter.html


async def test_missing_section_is_not_replaced_with_description(page, tmp_path):
    await page.set_content('<meta name="description" content="这不是正文"/>')
    await page.evaluate(
        """({key, decrypt}) => {
            window[key] = {reader:{bookId:'abc',currentChapter:{chapterUid:10},
                chapterContentState:'DONE',chapterContentHtml:[{value:'正文'},null]}};
            window[decrypt] = value => value;
        }""",
        {"key": INITIAL_STATE_REF, "decrypt": DECRYPTION},
    )
    with pytest.raises(ReaderError, match="分段尚未加载"):
        await download_chapter(
            ReaderPage(page),
            ChapterInfo(uid=10, index=0, title="第一章"),
            tmp_path,
        )


async def test_spa_chapter_navigation_waits_for_uid(page, tmp_path):
    await page.set_content("""<div class="readerCatalog_list"><div>
        <span class="readerCatalog_list_item_title_text" onclick="setTimeout(() => {
            window.__INITIAL_STATE__REF__.reader.currentChapter.chapterUid = 20;
            window.__INITIAL_STATE__REF__.reader.chapterContentHtml = [{value:'新章节'}];
        }, 50)">第二章</span></div></div>""")
    await page.evaluate(
        """({key, decrypt}) => {
            window[key] = {reader:{bookId:'abc',currentChapter:{chapterUid:10},
                chapterContentState:'DONE',chapterContentHtml:[{value:'旧章节'}]}};
            window[decrypt] = value => value;
        }""",
        {"key": INITIAL_STATE_REF, "decrypt": DECRYPTION},
    )
    chapter = await download_chapter(
        ReaderPage(page),
        ChapterInfo(uid=20, index=1, title="第二章"),
        tmp_path,
    )
    assert "新章节" in chapter.html and "旧章节" not in chapter.html


async def test_open_book_goes_directly_to_reader_without_start_button(page):
    visited = []

    async def route(request):
        visited.append(request.request.url)
        await request.fulfill(
            content_type="text/html; charset=utf-8",
            body="""<div class="wr_avatar" hidden></div><script>
                window.__INITIAL_STATE__REF__ = {reader:{
                    bookInfo:{title:"直接阅读",author:"测试作者"},
                    chapterInfos:[{chapterUid:10,title:"第一章",level:1}]
                }};
            </script>""",
        )

    await page.route("https://weread.qq.com/**", route)
    reader = ReaderPage(page)
    book = await open_book(reader, "abc123", headless=True)
    assert visited == ["https://weread.qq.com/web/reader/abc123"]
    assert book.title == "直接阅读"
    assert reader.chapter_infos[0].uid == 10
