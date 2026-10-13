# -*- coding: utf-8 -*-
"""TikTok Studio 网页版视频发布（patchright 版本，替代旧的 tk_uploader）。

图片帖会在上游被 sns_publish 合成为幻灯片视频后再走这里。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from patchright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS
from utils.log import tiktok_logger
from utils.web_session import (
    click_if_visible,
    has_cookie,
    launch,
    manual_login,
    new_context,
    save_debug_screenshot,
)

TT_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
TT_LOGIN = "https://www.tiktok.com/login?lang=en"
TT_CAPTION_LIMIT = 2200
POST_BUTTON = 'button[data-e2e="post_video_button"]'


async def tiktok_login(account_file: str) -> bool:
    return await manual_login(TT_LOGIN, account_file, "tiktok.com", "sessionid", tiktok_logger)


async def cookie_auth(account_file: str) -> bool:
    if not has_cookie(account_file, "tiktok.com", "sessionid"):
        return False
    async with async_playwright() as playwright:
        browser = await launch(playwright, headless=True)
        context = await new_context(browser, account_file)
        try:
            page = await context.new_page()
            await page.goto(TT_UPLOAD)
            await page.wait_for_timeout(5000)
            if "/login" in page.url:
                tiktok_logger.info("TikTok 登录态已失效")
                return False
            return True
        except Exception as exc:
            tiktok_logger.warning(f"TikTok 登录态校验出错，按失效处理: {exc}")
            return False
        finally:
            await context.close()
            await browser.close()


class TikTokStudioVideo:
    def __init__(
        self,
        file_path: str | Path,
        caption: str,
        tags: list[str],
        account_file: str,
        headless: bool = LOCAL_CHROME_HEADLESS,
    ):
        self.file_path = str(Path(file_path).resolve())
        self.caption = caption
        self.tags = tags
        self.account_file = account_file
        self.headless = headless

    async def _fill_caption(self, page: Page) -> None:
        editor = page.locator('div[contenteditable="true"]').first
        await editor.wait_for(state="visible", timeout=60000)
        await editor.click()
        # 编辑器默认会把文件名填进去，先清空
        await page.keyboard.press("ControlOrMeta+A")
        await page.keyboard.press("Delete")
        budget = TT_CAPTION_LIMIT - sum(len(t) + 2 for t in self.tags)
        await page.keyboard.insert_text(self.caption[: max(budget, 0)])
        for tag in self.tags:
            # 输入 #话题 会弹出联想框，空格可以把它关掉
            await page.keyboard.insert_text(f" #{tag}")
            await page.wait_for_timeout(1200)
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
        await page.wait_for_timeout(500)

    async def _wait_until_postable(self, page: Page, timeout_seconds: int = 15 * 60) -> None:
        button = page.locator(POST_BUTTON).first
        for _ in range(timeout_seconds // 2):
            if await button.count():
                disabled = await button.get_attribute("disabled")
                aria_disabled = await button.get_attribute("aria-disabled")
                loading = await button.get_attribute("data-loading")
                if disabled is None and aria_disabled != "true" and loading != "true":
                    return
            await asyncio.sleep(2)
        raise TimeoutError("TikTok 视频上传超时")

    async def upload(self) -> None:
        async with async_playwright() as playwright:
            browser = await launch(playwright, headless=self.headless)
            context = await new_context(browser, self.account_file)
            page = await context.new_page()
            try:
                await page.goto(TT_UPLOAD)
                await page.wait_for_timeout(3000)
                if "/login" in page.url:
                    raise RuntimeError("TikTok 登录态已失效，请先运行 `sns login tiktok`")

                tiktok_logger.info(f"上传视频 {Path(self.file_path).name}")
                file_input = page.locator('input[type="file"]').first
                await file_input.wait_for(state="attached", timeout=60000)
                await file_input.set_input_files(self.file_path)

                await self._fill_caption(page)
                # 首次使用时可能弹出“自动内容检查”等提示
                for name in ("Got it", "Cancel", "Not now"):
                    await click_if_visible(page, page.get_by_role("button", name=name, exact=True), timeout=800)

                await self._wait_until_postable(page)
                tiktok_logger.info("视频处理完毕，点击 Post")
                await page.locator(POST_BUTTON).first.click()
                # 有时会弹出 “Continue to post?” 确认框
                await click_if_visible(page, page.get_by_role("button", name="Post now", exact=True), timeout=5000)

                await page.wait_for_url("**/tiktokstudio/content**", timeout=3 * 60 * 1000)
                tiktok_logger.success("TikTok 发布成功")
                await context.storage_state(path=self.account_file)
            except Exception:
                shot = await save_debug_screenshot(page, f"tiktok_{datetime.now():%Y%m%d_%H%M%S}")
                if shot:
                    tiktok_logger.error(f"TikTok 发布失败，截图: {shot}")
                raise
            finally:
                await context.close()
                await browser.close()

    async def main(self) -> None:
        await self.upload()
