"""浏览器登录态工具：给没有扫码登录接口的平台（Instagram、TikTok）用。

流程：打开有界面的浏览器 -> 用户自己登录 -> 检测到会话 cookie 后保存 storage_state。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from patchright.async_api import BrowserContext, Page, async_playwright

from conf import BASE_DIR, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script

SCREENSHOT_DIR = Path(BASE_DIR) / "logs" / "screens"


async def launch(playwright, headless: bool):
    if LOCAL_CHROME_PATH:
        return await playwright.chromium.launch(headless=headless, executable_path=LOCAL_CHROME_PATH)
    return await playwright.chromium.launch(headless=headless, channel="chromium")


async def new_context(browser, account_file: str | None = None) -> BrowserContext:
    kwargs = {"locale": "en-US", "viewport": {"width": 1400, "height": 900}}
    if account_file and Path(account_file).exists():
        kwargs["storage_state"] = account_file
    context = await browser.new_context(**kwargs)
    return await set_init_script(context)


def has_cookie(account_file: str, domain_suffix: str, cookie_name: str) -> bool:
    path = Path(account_file)
    if not path.exists():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        c.get("name") == cookie_name and c.get("domain", "").endswith(domain_suffix) and c.get("value")
        for c in state.get("cookies", [])
    )


async def manual_login(
    login_url: str,
    account_file: str,
    domain_suffix: str,
    cookie_name: str,
    logger,
    timeout_seconds: int = 600,
) -> bool:
    """打开浏览器让用户手动登录，检测到会话 cookie 后保存登录态。"""
    Path(account_file).parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await launch(playwright, headless=False)
        context = await new_context(browser)
        try:
            page = await context.new_page()
            await page.goto(login_url)
            logger.info(f"请在弹出的浏览器里完成登录（最多等待 {timeout_seconds // 60} 分钟）")
            for _ in range(timeout_seconds // 2):
                cookies = await context.cookies()
                if any(
                    c["name"] == cookie_name and c["domain"].endswith(domain_suffix) and c["value"]
                    for c in cookies
                ):
                    # 等登录后的跳转和其余 cookie 落地
                    await asyncio.sleep(5)
                    await context.storage_state(path=account_file)
                    logger.success(f"登录成功，登录态已保存到 {account_file}")
                    return True
                await asyncio.sleep(2)
            logger.error("等待登录超时")
            return False
        finally:
            await context.close()
            await browser.close()


async def save_debug_screenshot(page: Page, name: str) -> Path | None:
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{name}.png"
        await page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None


async def click_if_visible(page: Page, locator, timeout: int = 1500) -> bool:
    try:
        target = locator.first
        await target.wait_for(state="visible", timeout=timeout)
        await target.click()
        return True
    except Exception:
        return False
