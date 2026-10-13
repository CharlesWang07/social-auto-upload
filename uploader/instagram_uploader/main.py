# -*- coding: utf-8 -*-
"""Instagram 网页版发布：图片（单图 / 轮播）和视频（自动作为 Reels）。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from patchright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS
from utils.log import instagram_logger
from utils.web_session import (
    click_if_visible,
    has_cookie,
    launch,
    manual_login,
    new_context,
    save_debug_screenshot,
)

IG_HOME = "https://www.instagram.com/"
IG_LOGIN = "https://www.instagram.com/accounts/login/"
IG_MAX_ITEMS = 20
IG_CAPTION_LIMIT = 2200
SHARED_TEXT = re.compile(r"(post|reel) has been shared|Post shared|Reel shared", re.I)


async def instagram_login(account_file: str) -> bool:
    return await manual_login(IG_LOGIN, account_file, "instagram.com", "sessionid", instagram_logger)


async def cookie_auth(account_file: str) -> bool:
    if not has_cookie(account_file, "instagram.com", "sessionid"):
        return False
    async with async_playwright() as playwright:
        browser = await launch(playwright, headless=True)
        context = await new_context(browser, account_file)
        try:
            page = await context.new_page()
            await page.goto(IG_HOME)
            await page.wait_for_timeout(4000)
            if "/accounts/login" in page.url or await page.locator('input[name="password"]').count():
                instagram_logger.info("Instagram 登录态已失效")
                return False
            return True
        except Exception as exc:
            instagram_logger.warning(f"Instagram 登录态校验出错，按失效处理: {exc}")
            return False
        finally:
            await context.close()
            await browser.close()


class InstagramPost:
    def __init__(self, files: list[str | Path], caption: str, account_file: str, headless: bool = LOCAL_CHROME_HEADLESS):
        self.files = [str(Path(f).resolve()) for f in files][:IG_MAX_ITEMS]
        self.caption = caption[:IG_CAPTION_LIMIT]
        self.account_file = account_file
        self.headless = headless

    async def _dismiss_popups(self, page: Page) -> None:
        for name in ("Not Now", "Not now", "Cancel"):
            await click_if_visible(page, page.get_by_role("button", name=name, exact=True), timeout=800)

    async def _open_create_dialog(self, page: Page) -> None:
        create = page.locator('svg[aria-label="New post"], svg[aria-label="Create"]').first
        await create.wait_for(state="visible", timeout=30000)
        await create.click()
        # 新版侧边栏点击 Create 后会先弹出 Post / Live / Ad 子菜单
        await click_if_visible(page, page.locator('svg[aria-label="Post"]'), timeout=2500) or await click_if_visible(
            page, page.get_by_text("Post", exact=True), timeout=1500
        )

    async def _click_dialog_button(self, page: Page, name: str, timeout: int = 30000) -> None:
        button = page.locator('div[role="dialog"]').get_by_role("button", name=name, exact=True).first
        await button.wait_for(state="visible", timeout=timeout)
        await button.click()

    async def _keep_original_crop(self, page: Page) -> None:
        # 默认会被裁成 1:1，切到 Original 保持原比例
        if await click_if_visible(page, page.locator('svg[aria-label="Select crop"]'), timeout=5000):
            await click_if_visible(page, page.get_by_text("Original", exact=True), timeout=3000)

    async def upload(self) -> None:
        if not self.files:
            raise ValueError("Instagram 发布至少需要一个文件")
        async with async_playwright() as playwright:
            browser = await launch(playwright, headless=self.headless)
            context = await new_context(browser, self.account_file)
            page = await context.new_page()
            try:
                await page.goto(IG_HOME)
                await page.wait_for_timeout(3000)
                if "/accounts/login" in page.url:
                    raise RuntimeError("Instagram 登录态已失效，请先运行 `sns login instagram`")
                await self._dismiss_popups(page)

                instagram_logger.info(f"打开发帖窗口，共 {len(self.files)} 个文件")
                await self._open_create_dialog(page)
                file_input = page.locator('div[role="dialog"] input[type="file"]').first
                await file_input.wait_for(state="attached", timeout=30000)
                await file_input.set_input_files(self.files)

                # 视频会弹 “Video posts are now shared as reels”
                await click_if_visible(page, page.get_by_role("button", name="OK", exact=True), timeout=4000)
                await self._keep_original_crop(page)

                await self._click_dialog_button(page, "Next", timeout=60000)  # 裁剪 -> 编辑
                await page.wait_for_timeout(1500)
                await self._click_dialog_button(page, "Next")  # 编辑 -> 文案
                await page.wait_for_timeout(1500)

                if self.caption:
                    instagram_logger.info("填写文案")
                    editor = page.locator('div[role="dialog"] div[contenteditable="true"]').first
                    await editor.wait_for(state="visible", timeout=30000)
                    await editor.click()
                    await page.keyboard.insert_text(self.caption)
                    await page.wait_for_timeout(1000)

                instagram_logger.info("点击 Share")
                await self._click_dialog_button(page, "Share")
                # 视频要等服务器处理，给足时间
                await page.get_by_text(SHARED_TEXT).first.wait_for(state="visible", timeout=10 * 60 * 1000)
                instagram_logger.success("Instagram 发布成功")
                await context.storage_state(path=self.account_file)
            except Exception:
                shot = await save_debug_screenshot(page, f"instagram_{datetime.now():%Y%m%d_%H%M%S}")
                if shot:
                    instagram_logger.error(f"Instagram 发布失败，截图: {shot}")
                raise
            finally:
                await context.close()
                await browser.close()

    async def main(self) -> None:
        await self.upload()
