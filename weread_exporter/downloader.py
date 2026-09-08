from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import random
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import click
from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import BrowserContext, Error, TimeoutError

from .browser import ReaderError, ReaderPage
from .content import asset_path, normalize_html
from .hooks import DECRYPTION, INITIAL_STATE_REF
from .models import IMAGE_SOURCE_VERSION, Book, BookConfig, Chapter, ChapterInfo, Config
from .storage import atomic_write, read_chapter, safe_name, save_book, save_chapter, select_chapters

SITE = "https://weread.qq.com"


def _image_label(url: str) -> str:
    # Signed CDN query parameters are temporary credentials, not useful in error messages.
    return urlparse(url)._replace(query="", fragment="").geturl()


def _validate_image_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    allowed = ("qq.com", "qpic.cn", "gtimg.com", "qlogo.cn", "gfp.tencent-cloud.com")
    if parsed.scheme != "https" or not any(
        hostname == host or hostname.endswith("." + host) for host in allowed
    ):
        raise ReaderError(f"插图来源不在受支持的微信 CDN 中：{_image_label(url)}")


async def _download_image(url: str, reader: ReaderPage) -> bytes:
    page = reader.page
    headers = {
        "Referer": page.url,
        "User-Agent": await page.evaluate("() => navigator.userAgent"),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    # Handle redirects explicitly so each CDN destination is validated before requesting it.
    for _ in range(6):
        _validate_image_url(url)
        try:
            response = await page.context.request.get(url, headers=headers, max_redirects=0)
        except Error as error:
            raise ReaderError(f"插图请求失败：{_image_label(url)}") from error
        try:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ReaderError(f"插图重定向缺少目标地址：{_image_label(url)}")
                url = urljoin(url, location)
                continue
            if not response.ok:
                raise ReaderError(f"插图下载失败（HTTP {response.status}）：{_image_label(url)}")
            data = await response.body()
            if not data:
                raise ReaderError(f"插图下载结果为空：{_image_label(url)}")
            if len(data) > 20_000_000:
                raise ReaderError(f"插图超过 20 MB：{_image_label(url)}")
            return data
        finally:
            await response.dispose()
    raise ReaderError(f"插图重定向次数过多：{_image_label(url)}")


async def _localize_images(html: str, reader: ReaderPage, directory: Path) -> str:
    soup = BeautifulSoup(normalize_html(html), "lxml")
    seen: dict[str, str] = {}
    for tag in soup.find_all("img"):
        reference = str(tag.get("src", ""))
        if not reference:
            raise ReaderError("章节插图缺少 src/data-src 地址")
        if reference in seen:
            tag["src"] = seen[reference]
            continue
        if reference.startswith("data:image/"):
            header, encoded = reference.split(",", 1)
            if ";base64" not in header or len(encoded) > 28_000_000:
                raise ReaderError("不支持的内嵌插图编码或插图过大")
            data = base64.b64decode(encoded, validate=True)
        else:
            url = urljoin(reader.page.url, reference)
            data = await _download_image(url, reader)
        if len(data) > 20_000_000:
            raise ReaderError("插图超过 20 MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                buffer = io.BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(buffer, "PNG")
                data = buffer.getvalue()
        except (OSError, Image.DecompressionBombError) as error:
            raise ReaderError(f"无法解析章节插图：{error}") from error
        local = f"assets/{hashlib.sha256(data).hexdigest()}.png"
        atomic_write(directory / local, data)
        tag["src"] = seen[reference] = local
    return (soup.body or soup).decode_contents()


async def open_book(reader: ReaderPage, book_id: str, headless: bool) -> Book:
    page = reader.page
    reader_url = f"{SITE}/web/reader/{book_id}"
    await page.goto(reader_url, wait_until="domcontentloaded")
    reader.check()
    try:
        await page.locator(".wr_avatar").first.wait_for(state="attached", timeout=4000)
    except TimeoutError:
        if headless:
            raise ReaderError(
                "登录已失效，请先运行 uv run weread-exporter login 扫码登录"
            ) from None
        click.echo("请在浏览器中扫码登录微信读书。")
        await asyncio.to_thread(click.confirm, "登录后继续", default=True, abort=True)
        try:
            await page.locator(".wr_avatar").first.wait_for(state="attached", timeout=5000)
        except TimeoutError:
            raise ReaderError("未检测到登录状态，请完成扫码登录后重试") from None
        # Login may navigate away from the reader; reload it with the saved session.
        await page.goto(reader_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_function(
            "key => window[key]?.reader?.chapterInfos?.length > 0",
            arg=INITIAL_STATE_REF,
        )
    except TimeoutError as error:
        reader.check()
        raise ReaderError("无法读取目录：页面结构或初始化脚本可能已改变") from error
    reader.check()
    metadata = await page.evaluate(
        """key => {
            const r = window[key].reader;
            return {title: r.bookInfo.title, author: r.bookInfo.author || '',
                chapters: r.chapterInfos.map((c, i) => ({uid: c.chapterUid, index: i,
                    title: c.title || `第${i + 1}章`, level: Math.max(1, c.level || 1)}))};
        }""",
        INITIAL_STATE_REF,
    )
    reader.chapter_infos = [ChapterInfo.model_validate(value) for value in metadata.pop("chapters")]
    return Book(id=book_id, source_url=f"{SITE}/web/bookDetail/{book_id}", chapters=[], **metadata)


async def download_chapter(reader: ReaderPage, info: ChapterInfo, directory: Path) -> Chapter:
    page = reader.page
    await page.wait_for_function(
        "key => window[key]?.reader?.currentChapter", arg=INITIAL_STATE_REF
    )
    horizontal = page.locator(".readerControls_item.isHorizontalReader")
    if await horizontal.count() and await horizontal.first.is_visible():
        await horizontal.first.click()
        await page.wait_for_function(
            "() => !document.querySelector('.readerControls_item.isHorizontalReader')",
        )
        await page.wait_for_function(
            "key => window[key]?.reader?.currentChapter", arg=INITIAL_STATE_REF
        )
    current = await page.evaluate(
        "key => window[key]?.reader?.currentChapter?.chapterUid",
        INITIAL_STATE_REF,
    )
    if current != info.uid:
        catalogue = page.locator(".readerCatalog_list")
        if not await catalogue.count() or not await catalogue.first.is_visible():
            await page.locator(".readerControls_item.catalog").click()
        title = catalogue.locator(".readerCatalog_list_item_title_text").filter(
            has_text=re.compile(r"^\s*" + re.escape(info.title) + r"\s*$")
        )
        # The ordinal distinguishes duplicate chapter names, unlike the original title map.
        ordinal = sum(c.title == info.title and c.index < info.index for c in reader.chapter_infos)
        await title.nth(ordinal).click()
    try:
        await page.wait_for_function(
            """({key, uid}) => {
                const r = window[key]?.reader;
                return r?.currentChapter?.chapterUid === uid && r.chapterContentState === 'DONE';
            }""",
            arg={"key": INITIAL_STATE_REF, "uid": info.uid},
        )
    except TimeoutError as error:
        reader.check()
        raise ReaderError(f"章节未就绪：{info.title}；请检查阅读权限或页面变化") from error
    reader.check()
    try:
        await page.wait_for_function("key => typeof window[key] === 'function'", arg=DECRYPTION)
    except TimeoutError as error:
        reader.check()
        raise ReaderError("未找到正文解密入口，阅读器脚本可能已更新") from error
    result = await page.evaluate(
        """({key, decryptKey, uid}) => {
            const r = window[key]?.reader;
            if (!r || r.currentChapter.chapterUid !== uid) return {error: '当前章节不匹配'};
            const decrypt = window[decryptKey];
            if (typeof decrypt !== 'function')
                return {error: '未找到正文解密入口，阅读器脚本可能已更新'};
            const sections = r.chapterContentHtml;
            if (!Array.isArray(sections) || !sections.length)
                return {error: '正文为空；请检查当前账号是否能阅读本章'};
            let html = [];
            for (let index = 0; index < sections.length; index++) {
                const section = sections[index];
                if (!section || typeof section.value !== 'string' || !section.value)
                    return {error: `第 ${index + 1} 个分段尚未加载，无法确认章节完整性`};
                try {
                    const value = decrypt(section.value, r.bookId, uid, index);
                    if (typeof value !== 'string' || !value.trim())
                        return {error: `第 ${index + 1} 个分段解密结果为空`};
                    html.push(value);
                } catch (error) { return {error: String(error)}; }
            }
            return {html};
        }""",
        {"key": INITIAL_STATE_REF, "decryptKey": DECRYPTION, "uid": info.uid},
    )
    if result.get("error"):
        raise ReaderError(f"{info.title}：{result['error']}")
    # Normalize separately: sections may each be a complete HTML document.
    html = "\n".join(normalize_html(section) for section in result["html"])
    html = await _localize_images(html, reader, directory)
    soup = BeautifulSoup(html, "lxml")
    if not soup.get_text(strip=True) and not soup.find("img"):
        raise ReaderError(f"{info.title}：正文没有有效文本或插图")
    return Chapter(**info.model_dump(), html=html, image_source_version=IMAGE_SOURCE_VERSION)


def _interactive_selection(chapters: list[ChapterInfo]) -> list[ChapterInfo]:
    for index, chapter in enumerate(chapters, 1):
        click.echo(f"{index:4d}. {'  ' * min(chapter.level - 1, 6)}{chapter.title}")
    answer = click.prompt("选择章节序号（如 1,3-5；all 表示全部）", default="all")
    if answer.strip().lower() == "all":
        return chapters
    indexes: set[int] = set()
    for part in answer.split(","):
        if not re.fullmatch(r"\s*\d+(?:\s*-\s*\d+)?\s*", part):
            raise ValueError(f"无效的章节序号：{part}")
        numbers = [int(value.strip()) for value in part.split("-")]
        start, end = numbers[0], numbers[-1]
        if not 1 <= start <= end <= len(chapters):
            raise ValueError(f"章节序号超出范围：{part}")
        indexes.update(range(start - 1, end))
    return [chapter for i, chapter in enumerate(chapters) if i in indexes]


async def download_book(
    context: BrowserContext,
    spec: BookConfig,
    config: Config,
    *,
    interactive: bool = False,
) -> tuple[Book, Path]:
    page = await context.new_page()
    reader = ReaderPage(page)
    await reader.install()
    try:
        book = await open_book(reader, spec.id, config.browser.headless)
        selected = (
            await asyncio.to_thread(_interactive_selection, reader.chapter_infos)
            if interactive
            else select_chapters(reader.chapter_infos, spec.chapters)
        )
        directory = config.weread.output / f"{safe_name(book.title)}_{book.id}"
        book.selected_chapter_uids = [chapter.uid for chapter in selected]
        save_book(book, directory)
        for count, info in enumerate(selected, 1):
            chapter = read_chapter(directory, info) if config.weread.enable_cache else None
            if chapter:
                for img in BeautifulSoup(chapter.html, "lxml").find_all("img"):
                    if asset_path(directory, str(img.get("src", ""))) is None:
                        chapter = None
                        break
            if chapter is None:
                for attempt in range(config.weread.retries + 1):
                    await asyncio.sleep(config.weread.delay * random.uniform(0.8, 1.2))
                    try:
                        chapter = await download_chapter(reader, info, directory)
                        save_chapter(directory, chapter)
                        break
                    except (ReaderError, Error) as error:
                        if attempt == config.weread.retries:
                            raise ReaderError(
                                f"下载失败，已完成章节保存在 {directory}，"
                                f"可用 --cache 续传：{error}"
                            ) from error
                        click.echo(
                            f"重试 {info.title} ({attempt + 1}/{config.weread.retries})", err=True
                        )
                        await page.reload(wait_until="domcontentloaded")
            else:
                click.echo(f"缓存命中：{info.title}")
            book.chapters.append(chapter)
            save_book(book, directory)
            click.echo(f"[{count}/{len(selected)}] {book.title} | {info.title}")
        return book, directory
    finally:
        await page.close()


async def login(context: BrowserContext) -> None:
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(SITE, wait_until="domcontentloaded")
    click.echo("请在浏览器中扫码登录微信读书。登录信息将保存到独立的 Python 浏览器目录。")
    await asyncio.to_thread(click.confirm, "登录后保存并退出", default=True, abort=True)
    try:
        await page.locator(".wr_avatar").first.wait_for(timeout=5000)
    except TimeoutError:
        raise ReaderError("未检测到登录状态，请重新运行 login") from None
    click.echo("登录状态已保存。")
