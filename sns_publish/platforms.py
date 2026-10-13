"""四个平台的统一发布入口：抖音、小红书复用上游 sau_cli，TikTok、Instagram 用新增的上传器。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sau_cli import (
    DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
    DOUYIN_PUBLISH_STRATEGY_SCHEDULED,
    XIAOHONGSHU_PUBLISH_STRATEGY_IMMEDIATE,
    XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED,
    DouyinNoteUploadRequest,
    DouyinVideoUploadRequest,
    XiaohongshuNoteUploadRequest,
    XiaohongshuVideoUploadRequest,
    check_douyin_account,
    check_xiaohongshu_account,
    login_douyin_account,
    login_xiaohongshu_account,
    resolve_account_file,
    upload_note as upload_douyin_note,
    upload_video as upload_douyin_video,
    upload_xiaohongshu_note,
    upload_xiaohongshu_video,
)
from sns_publish.captions import Caption
from sns_publish.media import PreparedMedia
from uploader.instagram_uploader.main import InstagramPost
from uploader.instagram_uploader.main import cookie_auth as instagram_cookie_auth
from uploader.instagram_uploader.main import instagram_login
from uploader.tiktok_studio_uploader.main import TikTokStudioVideo
from uploader.tiktok_studio_uploader.main import cookie_auth as tiktok_cookie_auth
from uploader.tiktok_studio_uploader.main import tiktok_login

XHS_MAX_IMAGES = 18
DOUYIN_MAX_IMAGES = 35


def _hashtags(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


async def login(platform: str, account: str) -> bool:
    if platform == "douyin":
        return bool((await login_douyin_account(account, headless=False)).get("success"))
    if platform == "xiaohongshu":
        return bool((await login_xiaohongshu_account(account, headless=False)).get("success"))
    if platform == "tiktok":
        return await tiktok_login(str(resolve_account_file("tiktok", account)))
    if platform == "instagram":
        return await instagram_login(str(resolve_account_file("instagram", account)))
    raise ValueError(f"不支持的平台: {platform}")


async def check(platform: str, account: str) -> bool:
    if platform == "douyin":
        return await check_douyin_account(account)
    if platform == "xiaohongshu":
        return await check_xiaohongshu_account(account)
    if platform == "tiktok":
        return await tiktok_cookie_auth(str(resolve_account_file("tiktok", account)))
    if platform == "instagram":
        return await instagram_cookie_auth(str(resolve_account_file("instagram", account)))
    raise ValueError(f"不支持的平台: {platform}")


async def publish(
    platform: str,
    account: str,
    media: PreparedMedia,
    caption: Caption,
    schedule: datetime | None,
    headless: bool,
) -> None:
    publish_date = schedule or 0

    if platform == "douyin":
        strategy = DOUYIN_PUBLISH_STRATEGY_SCHEDULED if schedule else DOUYIN_PUBLISH_STRATEGY_IMMEDIATE
        if media.kind == "images":
            await upload_douyin_note(DouyinNoteUploadRequest(
                account_name=account, image_files=media.images[:DOUYIN_MAX_IMAGES],
                title=caption.title, note=caption.desc, tags=caption.tags,
                publish_date=publish_date, publish_strategy=strategy, debug=False, headless=headless,
            ))
        else:
            await upload_douyin_video(DouyinVideoUploadRequest(
                account_name=account, video_file=media.video, title=caption.title,
                description=caption.desc, tags=caption.tags, publish_date=publish_date,
                publish_strategy=strategy, debug=False, headless=headless,
            ))
        return

    if platform == "xiaohongshu":
        strategy = XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED if schedule else XIAOHONGSHU_PUBLISH_STRATEGY_IMMEDIATE
        if media.kind == "images":
            await upload_xiaohongshu_note(XiaohongshuNoteUploadRequest(
                account_name=account, image_files=media.images[:XHS_MAX_IMAGES],
                title=caption.title, note=caption.desc, tags=caption.tags,
                publish_date=publish_date, publish_strategy=strategy, debug=False, headless=headless,
            ))
        else:
            await upload_xiaohongshu_video(XiaohongshuVideoUploadRequest(
                account_name=account, video_file=media.video, title=caption.title,
                description=caption.desc, tags=caption.tags, publish_date=publish_date,
                publish_strategy=strategy, debug=False, headless=headless,
            ))
        return

    if platform == "tiktok":
        await TikTokStudioVideo(
            media.tiktok_video, caption.as_single_text(), caption.tags,
            str(resolve_account_file("tiktok", account)), headless=headless,
        ).main()
        return

    if platform == "instagram":
        files: list[Path] = media.images if media.kind == "images" else [media.video]
        text = caption.as_single_text()
        if caption.tags:
            text = f"{text}\n\n{_hashtags(caption.tags)}"
        await InstagramPost(files, text, str(resolve_account_file("instagram", account)), headless=headless).main()
        return

    raise ValueError(f"不支持的平台: {platform}")
