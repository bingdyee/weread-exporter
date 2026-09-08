import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from weread_exporter.browser import ReaderError, ReaderPage
from weread_exporter.content import normalize_html
from weread_exporter.downloader import _localize_images
from weread_exporter.models import IMAGE_SOURCE_VERSION, ChapterInfo
from weread_exporter.storage import read_chapter, save_chapter

REAL_IMAGE = "https://res.weread.qq.com/wrepub/example.png"
READER_URL = "https://weread.qq.com/web/reader/abc123"
PLACEHOLDER = (
    "data:image/gif;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQImWNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def reader_with_responses(*responses):
    request = SimpleNamespace(get=AsyncMock(side_effect=responses))
    page = SimpleNamespace(
        url=READER_URL,
        evaluate=AsyncMock(return_value="Test Chrome"),
        context=SimpleNamespace(request=request),
    )
    return ReaderPage(page)


def response(body=b"", status=200, headers=None):
    return SimpleNamespace(
        status=status,
        ok=200 <= status < 300,
        headers=headers or {},
        body=AsyncMock(return_value=body),
        dispose=AsyncMock(),
    )


@pytest.fixture
def real_png():
    stream = io.BytesIO()
    Image.new("RGB", (80, 40), color="#335577").save(stream, "PNG")
    return stream.getvalue()


def test_lazy_source_wins_over_transparent_placeholder():
    html = f'<img src="{PLACEHOLDER}" data-src="{REAL_IMAGE}" alt="图 1"/>'
    normalized = normalize_html(html)
    tag = BeautifulSoup(normalized, "lxml").img
    assert tag["src"] == REAL_IMAGE
    assert tag["alt"] == "图 1"
    assert "data-src" not in tag.attrs
    assert normalize_html(normalized) == normalized


async def test_download_real_lazy_image_and_reuse_repeated_url(tmp_path, real_png):
    image_response = response(real_png)
    reader = reader_with_responses(image_response)
    html = f'<img src="{PLACEHOLDER}" data-src="{REAL_IMAGE}"/>' * 2
    normalized = await _localize_images(html, reader, tmp_path)
    images = BeautifulSoup(normalized, "lxml").find_all("img")
    assert len(images) == 2 and images[0]["src"] == images[1]["src"]
    assert len(list((tmp_path / "assets").iterdir())) == 1
    with Image.open(tmp_path / images[0]["src"]) as image:
        assert image.size == (80, 40)
    reader.page.context.request.get.assert_awaited_once_with(
        REAL_IMAGE,
        headers={
            "Referer": READER_URL,
            "User-Agent": "Test Chrome",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
        max_redirects=0,
    )
    image_response.dispose.assert_awaited_once()


async def test_image_redirects_keep_reader_headers(tmp_path, real_png):
    destination = "https://wrco-40036.sh.gfp.tencent-cloud.com/book/image.png?sign=example"
    redirect = response(status=302, headers={"location": destination})
    image_response = response(real_png)
    reader = reader_with_responses(redirect, image_response)
    await _localize_images(f'<img src="{REAL_IMAGE}"/>', reader, tmp_path)
    calls = reader.page.context.request.get.await_args_list
    assert [call.args[0] for call in calls] == [REAL_IMAGE, destination]
    assert all(call.kwargs["headers"]["Referer"] == READER_URL for call in calls)
    redirect.dispose.assert_awaited_once()
    image_response.dispose.assert_awaited_once()


async def test_untrusted_redirect_is_not_requested(tmp_path):
    redirect = response(status=302, headers={"location": "http://127.0.0.1/private.png"})
    reader = reader_with_responses(redirect)
    with pytest.raises(ReaderError, match="CDN"):
        await _localize_images(f'<img src="{REAL_IMAGE}"/>', reader, tmp_path)
    reader.page.context.request.get.assert_awaited_once()
    assert not (tmp_path / "assets").exists()


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_image_http_failures_are_not_saved(tmp_path, status):
    image_response = response(b"error", status=status)
    reader = reader_with_responses(image_response)
    with pytest.raises(ReaderError, match=str(status)):
        await _localize_images(f'<img src="{REAL_IMAGE}"/>', reader, tmp_path)
    image_response.dispose.assert_awaited_once()
    assert not (tmp_path / "assets").exists()


async def test_invalid_image_is_not_saved(tmp_path):
    reader = reader_with_responses(response(b"<html>forbidden</html>"))
    with pytest.raises(ReaderError, match="无法解析"):
        await _localize_images(f'<img src="{REAL_IMAGE}"/>', reader, tmp_path)
    assert not (tmp_path / "assets").exists()


async def test_signed_image_credentials_are_not_in_error(tmp_path):
    reader = reader_with_responses(response(status=403))
    with pytest.raises(ReaderError) as error:
        await _localize_images(f'<img src="{REAL_IMAGE}?sign=temporary-secret"/>', reader, tmp_path)
    assert REAL_IMAGE in str(error.value)
    assert "temporary-secret" not in str(error.value)


async def test_inline_images_still_work(tmp_path, real_png):
    reader = reader_with_responses()
    reference = "data:image/png;base64," + base64.b64encode(real_png).decode()
    html = await _localize_images(f'<img src="{reference}"/>', reader, tmp_path)
    with Image.open(tmp_path / BeautifulSoup(html, "lxml").img["src"]) as image:
        assert image.size == (80, 40)
    reader.page.context.request.get.assert_not_awaited()


def test_old_image_cache_requires_redownload(tmp_path, sample_book):
    chapter = sample_book.chapters[0]
    info = ChapterInfo(**chapter.model_dump(include=set(ChapterInfo.model_fields)))
    chapter.html += '<img src="assets/placeholder.png"/>'
    save_chapter(tmp_path, chapter)
    assert read_chapter(tmp_path, info) is None
    chapter.image_source_version = IMAGE_SOURCE_VERSION
    save_chapter(tmp_path, chapter)
    assert read_chapter(tmp_path, info) == chapter
