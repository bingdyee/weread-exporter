"""Validated configuration and portable book manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Format = Literal["epub", "pdf", "markdown", "txt"]
FORMATS = ("epub", "pdf", "markdown", "txt")
IMAGE_SOURCE_VERSION = 2


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ChapterInfo(Model):
    uid: int
    index: int = Field(ge=0)
    title: str
    level: int = Field(default=1, ge=1)


class Chapter(ChapterInfo):
    html: str = Field(min_length=1)
    image_source_version: int = Field(default=1, ge=1)


class Book(Model):
    schema_version: Literal[1] = 1
    id: str
    title: str
    author: str = ""
    source_url: str = ""
    selected_chapter_uids: list[int] = Field(default_factory=list)
    chapters: list[Chapter]


class BookConfig(Model):
    id: str
    chapters: list[str] = Field(default_factory=lambda: ["..."])
    formats: list[Format] | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValueError("书籍 ID 只能包含字母和数字")
        return value


class BrowserConfig(Model):
    executable_path: str | None = Field(default=None, alias="executablePath")
    channel: str | None = None
    user_data_dir: Path = Field(default=Path("data/user-data"), alias="userDataDir")
    headless: bool = False
    timeout: float = Field(default=30, gt=0, description="seconds")


class WeReadConfig(Model):
    books: list[BookConfig] = Field(default_factory=list)
    enable_cache: bool = Field(default=False, alias="enableCache")
    formats: list[Format] = Field(default_factory=lambda: ["txt"], min_length=1)
    output: Path = Path("output")
    delay: float = Field(default=1, ge=0)
    retries: int = Field(default=2, ge=0, le=10)


class Config(Model):
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    weread: WeReadConfig = Field(default_factory=WeReadConfig)


def read_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = json.loads(path.read_text(encoding="utf-8"))
    # Accept the existing TypeScript config without requiring Puppeteer in Python.
    if isinstance(data, dict) and "puppeteer" in data:
        legacy = data.pop("puppeteer")
        if not isinstance(legacy, dict) or set(legacy) - {"launch"}:
            raise ValueError("puppeteer 配置仅支持 launch.executablePath")
        launch = legacy.get("launch", {})
        if not isinstance(launch, dict) or set(launch) - {"executablePath"}:
            raise ValueError("puppeteer 配置仅支持 launch.executablePath")
        data["browser"] = {**launch, **data.get("browser", {})}
    # Older configs used combine to request whole-book TXT/Markdown. This is now unconditional.
    if isinstance(data, dict) and isinstance(data.get("weread"), dict):
        books = data["weread"].get("books")
        if isinstance(books, list):
            for book in books:
                if isinstance(book, dict):
                    book.pop("combine", None)
    return Config.model_validate(data)
