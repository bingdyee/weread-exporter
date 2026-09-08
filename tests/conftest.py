import base64

import pytest

from weread_exporter.models import Book, Chapter


@pytest.fixture
def sample_book():
    return Book(
        id="example123",
        title="中文导出测试",
        author="测试作者",
        source_url="https://weread.qq.com/web/bookDetail/example123",
        chapters=[
            Chapter(
                uid=10,
                index=0,
                title="第一章 起点",
                level=1,
                html="<p>这是第一段中文。<strong>重点文字</strong>与 English 混排。</p>"
                "<p>第二段必须另起一行。<br/>这一行有换行。</p>"
                "<ul><li>第一项</li><li>第二项</li></ul>"
                "<table><tr><th>名称</th><th>说明</th></tr>"
                "<tr><td>格式</td><td>EPUB、PDF、Markdown、TXT</td></tr></table>",
            ),
            Chapter(uid=20, index=1, title="第二章 终点", html="<p>最后一章的正文。结尾。</p>"),
        ],
    )


@pytest.fixture
def png():
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1kAAAAASUVORK5CYII="
    )
