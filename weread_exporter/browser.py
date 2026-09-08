"""Playwright browser session and response interception."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager

from playwright.async_api import BrowserContext, Page, Route, async_playwright

from .hooks import HookError, is_script_url, is_site_url, override_document, override_script
from .models import BrowserConfig, ChapterInfo


class ReaderError(RuntimeError):
    pass


class ReaderPage:
    def __init__(self, page: Page):
        self.page = page
        self.errors: list[str] = []
        self.scripts: dict[tuple[str, str], str] = {}
        self.chapter_infos: list[ChapterInfo] = []

    async def install(self) -> None:
        await self.page.route("**/*", self._route)

    async def _route(self, route: Route) -> None:
        request = route.request
        document = request.resource_type == "document" and is_site_url(request.url)
        script = request.resource_type == "script" and is_script_url(request.url)
        if not document and not script:
            await route.continue_()
            return
        try:
            response = await route.fetch(max_redirects=0)
            try:
                if not 200 <= response.status < 300:
                    await route.fulfill(response=response)
                    return
                original = await response.text()
                if document:
                    patched = override_document(request.url, original)
                else:
                    key = (request.url, hashlib.sha256(original.encode()).hexdigest())
                    patched = self.scripts.get(key)
                    if patched is None:
                        patched = await asyncio.to_thread(override_script, request.url, original)
                        self.scripts[key] = patched
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    not in {"content-length", "content-encoding", "transfer-encoding"}
                }
                await route.fulfill(response=response, headers=headers, body=patched)
            finally:
                await response.dispose()
        except Exception as error:
            self.errors.append(f"{request.url}: {error}")
            await route.abort()

    def check(self) -> None:
        if self.errors:
            raise HookError("网页脚本拦截失败：" + self.errors[-1])


@asynccontextmanager
async def browser_session(config: BrowserConfig):
    config.user_data_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.user_data_dir),
            executable_path=config.executable_path,
            channel=config.channel,
            headless=config.headless,
            no_viewport=True,
            service_workers="block",
            args=["--disable-features=MacAppCodeSignClone"],
        )
        context.set_default_timeout(config.timeout * 1000)
        context.set_default_navigation_timeout(config.timeout * 1000)
        try:
            yield context
        finally:
            await context.close()
