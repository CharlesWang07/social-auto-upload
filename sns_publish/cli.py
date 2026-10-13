"""sns：把一个文件夹里的照片 / 视频一键发到抖音、小红书、TikTok、Instagram。

    sns login all                # 首次使用：依次登录四个平台
    sns check                    # 检查各平台登录态
    sns publish                  # 发布收件箱（默认 ~/Desktop/SNS上传/待发布）里的所有帖子
    sns publish 某个帖子文件夹      # 只发这一个
    sns publish --dry-run        # 只整理素材和生成文案，不发布
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from conf import BASE_DIR
from sns_publish import platforms as platform_api
from sns_publish.captions import PLATFORMS, resolve_captions
from sns_publish.media import BUILD_DIR_NAME, list_media, prepare
from utils.log import sns_logger

CONFIG_FILE = Path(BASE_DIR) / "sns_config.yaml"
STATE_FILE = ".sns_state.json"
DEFAULT_CONFIG = {
    "platforms": list(PLATFORMS),
    "account": "default",
    "inbox": "~/Desktop/SNS上传/待发布",
    "done_dir": "~/Desktop/SNS上传/已发布",
    "ai_captions": True,
    "model": "claude-opus-5-5",
    "overseas_language": "English",
    "headless": True,
    "timeout_minutes": 20,
}
PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "tiktok": "TikTok", "instagram": "Instagram"}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        config.update(yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {})
    return config


def _load_state(post_dir: Path) -> dict:
    path = post_dir / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_state(post_dir: Path, state: dict) -> None:
    (post_dir / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_posts(target: Path) -> list[Path]:
    """target 自己有素材就当成一条帖子；否则把每个有素材的子文件夹当成一条帖子。"""
    images, videos = list_media(target)
    if images or videos:
        return [target]
    return [
        d for d in sorted(target.iterdir())
        if d.is_dir() and not d.name.startswith(".") and any(files for files in list_media(d))
    ]


def _parse_schedule(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M")


async def publish_post(post_dir: Path, config: dict, only: list[str] | None, dry_run: bool) -> bool:
    sns_logger.info(f"===== 处理帖子：{post_dir.name} =====")
    media = await asyncio.to_thread(prepare, post_dir)
    sns_logger.info(f"素材整理完毕：{'图文' if media.kind == 'images' else '视频'}")

    data, captions = await asyncio.to_thread(
        resolve_captions, post_dir, media.preview_frames, media.kind,
        bool(config["ai_captions"]), config["model"], config["overseas_language"],
    )
    targets = only or data.get("platforms") or config["platforms"]
    schedule = _parse_schedule(data.get("schedule"))

    for platform in targets:
        c = captions[platform]
        sns_logger.info(f"[{PLATFORM_NAMES[platform]}] 标题：{c.title} | 话题：{' '.join('#' + t for t in c.tags)}")
    if dry_run:
        sns_logger.info(f"dry-run：未发布。素材在 {post_dir / BUILD_DIR_NAME}，文案可在 post.yaml 里修改")
        return False

    state = _load_state(post_dir)
    timeout = float(config["timeout_minutes"]) * 60
    for platform in targets:
        name = PLATFORM_NAMES[platform]
        if state.get(platform, {}).get("status") == "done":
            sns_logger.info(f"[{name}] 之前已发布，跳过")
            continue
        if schedule and platform in ("tiktok", "instagram"):
            sns_logger.warning(f"[{name}] 不支持定时发布，将立即发布")
        sns_logger.info(f"[{name}] 开始发布")
        try:
            await asyncio.wait_for(
                platform_api.publish(
                    platform, config["account"], media, captions[platform],
                    schedule if platform in ("douyin", "xiaohongshu") else None,
                    bool(config["headless"]),
                ),
                timeout=timeout,
            )
            state[platform] = {"status": "done", "at": datetime.now().isoformat(timespec="seconds")}
            sns_logger.success(f"[{name}] 发布成功")
        except Exception as exc:  # 一个平台失败不影响其他平台
            reason = "超时" if isinstance(exc, asyncio.TimeoutError) else str(exc) or type(exc).__name__
            state[platform] = {"status": "failed", "error": reason, "at": datetime.now().isoformat(timespec="seconds")}
            sns_logger.error(f"[{name}] 发布失败：{reason}")
        _save_state(post_dir, state)

    all_done = all(state.get(p, {}).get("status") == "done" for p in targets)
    if all_done:
        done_dir = Path(config["done_dir"]).expanduser()
        done_dir.mkdir(parents=True, exist_ok=True)
        dest = done_dir / f"{datetime.now():%Y%m%d}_{post_dir.name}"
        shutil.move(str(post_dir), str(dest))
        sns_logger.success(f"全部平台发布完成，已移动到 {dest}")
    else:
        failed = [PLATFORM_NAMES[p] for p in targets if state.get(p, {}).get("status") != "done"]
        sns_logger.warning(f"未完成：{', '.join(failed)}。修好后重新运行同一条命令，只会补发失败的平台")
    return all_done


async def cmd_publish(args, config: dict) -> int:
    if args.headed:
        config["headless"] = False
    if args.no_ai:
        config["ai_captions"] = False
    target = Path(args.path or config["inbox"]).expanduser().resolve()
    if not target.exists():
        target.mkdir(parents=True)
        sns_logger.info(f"已创建收件箱 {target}，把每次要发的照片/视频放进一个子文件夹即可")
        return 0
    posts = find_posts(target)
    if not posts:
        sns_logger.info(f"{target} 里没有待发布的内容")
        return 0
    ok = True
    for post_dir in posts:
        try:
            ok = await publish_post(post_dir, config, args.platforms, args.dry_run) and ok
        except Exception as exc:
            ok = False
            sns_logger.error(f"{post_dir.name} 处理失败：{exc}")
    return 0 if ok or args.dry_run else 1


async def cmd_login(args, config: dict) -> int:
    targets = list(PLATFORMS) if args.platform == "all" else [args.platform]
    ok = True
    for platform in targets:
        sns_logger.info(f"登录 {PLATFORM_NAMES[platform]}（扫码或在弹出的浏览器里登录）")
        success = await platform_api.login(platform, config["account"])
        ok = ok and success
        (sns_logger.success if success else sns_logger.error)(
            f"{PLATFORM_NAMES[platform]} 登录{'成功' if success else '失败'}"
        )
    return 0 if ok else 1


async def cmd_check(args, config: dict) -> int:
    ok = True
    for platform in config["platforms"]:
        valid = await platform_api.check(platform, config["account"])
        ok = ok and valid
        print(f"{PLATFORM_NAMES[platform]:<10} {'✅ 已登录' if valid else '❌ 未登录或已过期，运行 sns login ' + platform}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sns", description="一键把照片/视频发到抖音、小红书、TikTok、Instagram")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="登录平台（保存登录态）")
    p_login.add_argument("platform", choices=[*PLATFORMS, "all"])

    sub.add_parser("check", help="检查各平台登录态")

    p_pub = sub.add_parser("publish", help="发布帖子")
    p_pub.add_argument("path", nargs="?", help="帖子文件夹或收件箱，默认用配置里的 inbox")
    p_pub.add_argument("--platforms", nargs="+", choices=PLATFORMS, help="只发这些平台")
    p_pub.add_argument("--dry-run", action="store_true", help="只整理素材和生成文案，不发布")
    p_pub.add_argument("--headed", action="store_true", help="显示浏览器窗口（方便排查问题）")
    p_pub.add_argument("--no-ai", action="store_true", help="不用 AI 生成文案")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    handler = {"publish": cmd_publish, "login": cmd_login, "check": cmd_check}[args.command]
    return asyncio.run(handler(args, config))


if __name__ == "__main__":
    sys.exit(main())
