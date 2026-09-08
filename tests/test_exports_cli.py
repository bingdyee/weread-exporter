import hashlib
import zipfile

import pytest
from click.testing import CliRunner
from lxml import etree
from pypdf import PdfReader

from weread_exporter import exporters
from weread_exporter.cli import cli
from weread_exporter.content import html_to_text, normalize_html
from weread_exporter.exporters import export_book
from weread_exporter.storage import save_book


def test_all_formats_and_embedded_image(tmp_path, sample_book, png):
    asset = f"assets/{hashlib.sha256(png).hexdigest()}.png"
    path = tmp_path / asset
    path.parent.mkdir()
    path.write_bytes(png)
    sample_book.chapters[0].html += f'<p><img src="{asset}" alt="示例插图"/></p>'
    output = tmp_path / "exported"
    files = export_book(
        sample_book,
        tmp_path,
        output,
        ["epub", "pdf", "markdown", "txt"],
    )
    assert {path.name for path in files} == {
        "中文导出测试.epub",
        "中文导出测试.pdf",
        "中文导出测试.md",
        "中文导出测试.txt",
    }
    assert {path for path in output.rglob("*") if path.is_file()} == {*files, output / asset}
    assert all(path.stat().st_size for path in files)
    assert (output / asset).read_bytes() == png
    text = (output / "中文导出测试.txt").read_text()
    assert text.index("第一章") < text.index("第二章")
    assert "重点文字与 English" in text
    assert "\n\n第二段" in text
    assert "[图片：示例插图]" in text
    assert asset in (output / "中文导出测试.md").read_text()
    with zipfile.ZipFile(output / "中文导出测试.epub") as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "EPUB/" + asset in archive.namelist()
        for name in archive.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx")):
                etree.fromstring(archive.read(name))
        assert "第二章" in archive.read("EPUB/nav.xhtml").decode()
    pdf = PdfReader(output / "中文导出测试.pdf")
    assert len(pdf.pages) >= 3
    extracted = "".join(page.extract_text() for page in pdf.pages)
    assert "中文导出测试" in extracted
    assert "最后一章的正文" in extracted


@pytest.mark.parametrize("format_name, extension", [("txt", "txt"), ("markdown", "md")])
def test_offline_export_cli(tmp_path, sample_book, format_name, extension):
    save_book(sample_book, tmp_path)
    result = CliRunner().invoke(cli, ["export", str(tmp_path), "-f", format_name])
    assert result.exit_code == 0, result.output
    assert {path.name for path in tmp_path.iterdir()} == {"book.json", f"中文导出测试.{extension}"}
    content = (tmp_path / f"中文导出测试.{extension}").read_text()
    assert content.index("第一章") < content.index("第二章")
    assert "最后一章的正文" in content


@pytest.mark.parametrize("format_name, extension", [("txt", "txt"), ("markdown", "md")])
def test_text_export_only_writes_whole_book(
    tmp_path, sample_book, monkeypatch, format_name, extension
):
    writes = []
    original_write = exporters.atomic_write

    def record_write(path, content):
        writes.append(path)
        original_write(path, content)

    monkeypatch.setattr(exporters, "atomic_write", record_write)
    target = tmp_path / f"中文导出测试.{extension}"
    # Repeated format options must not create repeated files or intermediate chapter files.
    assert export_book(sample_book, tmp_path, tmp_path, [format_name, format_name]) == [target]
    assert writes == [target]
    assert list(tmp_path.iterdir()) == [target]


def test_failed_conversion_does_not_leave_chapter_files(tmp_path, sample_book, monkeypatch):
    target = tmp_path / "中文导出测试.txt"
    target.write_text("之前成功导出的整本内容")
    original_convert = exporters.html_to_text

    def fail_second_chapter(html):
        if "最后一章" in html:
            raise ValueError("conversion failed")
        return original_convert(html)

    monkeypatch.setattr(exporters, "html_to_text", fail_second_chapter)
    with pytest.raises(ValueError, match="conversion failed"):
        export_book(sample_book, tmp_path, tmp_path, ["txt"])
    assert target.read_text() == "之前成功导出的整本内容"
    assert list(tmp_path.iterdir()) == [target]


def test_cli_validation_happens_without_browser():
    runner = CliRunner()
    assert runner.invoke(cli, ["--help"]).exit_code == 0
    assert runner.invoke(cli, ["download", "--help"]).exit_code == 0
    assert runner.invoke(cli, ["download", "abc", "--all", "-c", "chapter"]).exit_code == 2
    assert runner.invoke(cli, ["download", "../abc", "--all"]).exit_code == 1


@pytest.mark.parametrize("format_name", ["epub", "pdf", "markdown", "txt"])
def test_incomplete_book_cannot_be_exported(tmp_path, sample_book, format_name):
    sample_book.selected_chapter_uids = [10, 20, 30]
    output = tmp_path / "exported"
    with pytest.raises(ValueError, match="续传"):
        export_book(sample_book, tmp_path, output, [format_name])
    assert not output.exists()


def test_unsafe_html_removed_and_inline_text_preserved():
    source = '<script>alert(1)</script><p onclick="evil()">这是<strong>中文</strong>段落</p>'
    assert "script" not in normalize_html(source)
    assert "onclick" not in normalize_html(source)
    assert html_to_text(source) == "这是中文段落"


def test_image_cannot_read_arbitrary_local_file(tmp_path, sample_book):
    sample_book.chapters[0].html = '<img src="../../private.png"/>'
    with pytest.raises(ValueError, match="路径无效"):
        export_book(sample_book, tmp_path, tmp_path, ["epub"])
