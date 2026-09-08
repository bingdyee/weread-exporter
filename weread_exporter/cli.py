from __future__ import annotations

import asyncio
from functools import wraps
from pathlib import Path

import click
from playwright.async_api import Error as PlaywrightError

from . import __version__
from .browser import ReaderError, browser_session
from .downloader import download_book
from .downloader import login as browser_login
from .exporters import export_book
from .hooks import HookError
from .models import FORMATS, BookConfig, Config, read_config
from .storage import read_book


def errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, ReaderError, HookError, PlaywrightError) as error:
            raise click.ClickException(str(error)) from error

    return wrapped


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
def cli():
    """微信读书下载器：导出 EPUB、PDF、Markdown、TXT。"""


def browser_options(function):
    for option in [
        click.option(
            "--config",
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="JSON 配置文件，也兼容原 TypeScript 配置。",
        ),
        click.option(
            "--browser-path",
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="Chrome/Chromium 可执行文件路径。",
        ),
        click.option(
            "--channel",
            type=click.Choice(["chrome", "msedge", "chromium"]),
            help="使用已安装的浏览器。",
        ),
        click.option(
            "--user-data-dir", type=click.Path(path_type=Path), help="独立浏览器用户目录。"
        ),
    ]:
        function = option(function)
    return function


def _config(config, browser_path, channel, user_data_dir) -> Config:
    if config is None:
        default = Path("config/config.json")
        config = default if default.is_file() else None
    result = read_config(config)
    if browser_path is not None:
        result.browser.executable_path = str(browser_path)
    if channel is not None:
        result.browser.channel = channel
        if browser_path is None:
            result.browser.executable_path = None
    if user_data_dir is not None:
        result.browser.user_data_dir = user_data_dir
    return result


@cli.command()
@browser_options
@errors
def login(config, browser_path, channel, user_data_dir):
    """打开浏览器扫码登录，保存登录状态。"""
    settings = _config(config, browser_path, channel, user_data_dir)
    settings.browser.headless = False

    async def run():
        async with browser_session(settings.browser) as context:
            await browser_login(context)

    asyncio.run(run())


@cli.command()
@click.argument("book_id", required=False)
@browser_options
@click.option(
    "-c", "--chapter", "chapters", multiple=True, help="章节完整名称，可重复；用 ... 表示范围。"
)
@click.option("-a", "--all", "all_chapters", is_flag=True, help="下载全部章节。")
@click.option(
    "-f",
    "--format",
    "formats",
    multiple=True,
    type=click.Choice(FORMATS),
    help="导出格式，可重复；默认 txt。",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="输出目录，默认 output。",
)
@click.option("--cache/--no-cache", default=None, help="复用完整章节缓存，支持中断后续传。")
@click.option("--headless/--headed", default=None, help="无界面模式需要已登录。")
@click.option("--delay", type=click.FloatRange(min=0), help="章节操作间隔秒数，默认 1。")
@click.option("--retries", type=click.IntRange(0, 10), help="失败重试次数，默认 2。")
@click.option(
    "--pdf-font",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="PDF 中文 TTF/TTC 字体。",
)
@errors
def download(
    book_id,
    config,
    browser_path,
    channel,
    user_data_dir,
    chapters,
    all_chapters,
    formats,
    output,
    cache,
    headless,
    delay,
    retries,
    pdf_font,
):
    """下载 BOOK_ID；未提供 ID 时读取配置或交互选择。"""
    if chapters and all_chapters:
        raise click.UsageError("--all 与 --chapter 不能同时使用")
    settings = _config(config, browser_path, channel, user_data_dir)
    for key, value in [
        ("output", output),
        ("enable_cache", cache),
        ("delay", delay),
        ("retries", retries),
    ]:
        if value is not None:
            setattr(settings.weread, key, value)
    if headless is not None:
        settings.browser.headless = headless
    interactive = False
    if book_id:
        specs = [BookConfig(id=book_id, chapters=list(chapters) or ["..."])]
        interactive = not chapters and not all_chapters
    elif settings.weread.books:
        specs = [spec.model_copy(deep=True) for spec in settings.weread.books]
        for spec in specs:
            if chapters or all_chapters:
                spec.chapters = list(chapters) or ["..."]
    else:
        book_id = click.prompt("请输入书籍 ID")
        specs = [BookConfig(id=book_id, chapters=list(chapters) or ["..."])]
        interactive = not chapters and not all_chapters

    async def run():
        async with browser_session(settings.browser) as context:
            # Sequential books avoid competing terminal login/selection prompts and page focus.
            for spec in specs:
                book, directory = await download_book(
                    context, spec, settings, interactive=interactive
                )
                chosen = list(formats) or spec.formats or settings.weread.formats
                files = await asyncio.to_thread(
                    export_book,
                    book,
                    directory,
                    directory,
                    chosen,
                    pdf_font=pdf_font,
                )
                click.echo(f"缓存：{directory / 'book.json'}")
                for file in files:
                    click.echo(f"已导出：{file}")

    asyncio.run(run())


@cli.command(name="export")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-f",
    "--format",
    "formats",
    multiple=True,
    type=click.Choice(FORMATS),
    required=True,
    help="目标格式，可重复。",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="默认导出到缓存所在目录。",
)
@click.option(
    "--pdf-font",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="PDF 中文 TTF/TTC 字体。",
)
@errors
def export_command(source, formats, output, pdf_font):
    """从 SOURCE（book.json 或书籍目录）离线导出，每种格式生成一个整本文件。"""
    book, directory = read_book(source)
    for file in export_book(
        book,
        directory,
        output or directory,
        list(formats),
        pdf_font=pdf_font,
    ):
        click.echo(f"已导出：{file}")
