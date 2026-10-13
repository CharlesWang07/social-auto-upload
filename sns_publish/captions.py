"""各平台文案：优先读帖子文件夹里的 post.yaml，没写的部分用 Claude 看图生成。

post.yaml 示例（全部字段可选）：

    hint: 周末和朋友去镰仓，看海、吃了吻仔鱼盖饭
    title: 镰仓一日游
    desc: 海风好舒服
    tags: [镰仓, 日本旅行]
    platforms: [douyin, xiaohongshu]   # 只发这几个平台
    schedule: "2026-10-20 20:00"       # 仅抖音 / 小红书支持定时
    tiktok:                             # 单独覆盖某个平台
      desc: Weekend in Kamakura
      tags: [kamakura, japan]
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import Image
from pydantic import BaseModel

from utils.log import sns_logger

PLATFORMS = ("douyin", "xiaohongshu", "tiktok", "instagram")
POST_FILE = "post.yaml"
TITLE_LIMITS = {"xiaohongshu": 20, "douyin": 30, "tiktok": 90, "instagram": 90}
TAG_LIMITS = {"xiaohongshu": 10, "douyin": 5, "tiktok": 8, "instagram": 30}


@dataclass
class Caption:
    title: str = ""
    desc: str = ""
    tags: list[str] = field(default_factory=list)

    def clipped(self, platform: str) -> "Caption":
        tags = [t.strip().lstrip("#") for t in self.tags if t and t.strip()]
        return Caption(
            title=self.title.strip()[: TITLE_LIMITS[platform]],
            desc=self.desc.strip(),
            tags=list(dict.fromkeys(tags))[: TAG_LIMITS[platform]],
        )

    def as_single_text(self) -> str:
        """TikTok / Instagram 没有单独标题，拼成一段文案（话题另外处理）。"""
        if not self.desc:
            return self.title
        if not self.title or self.title in self.desc:
            return self.desc
        return f"{self.title}\n\n{self.desc}"


class _PlatformCaption(BaseModel):
    title: str
    desc: str
    tags: list[str]


class _GeneratedCaptions(BaseModel):
    xiaohongshu: _PlatformCaption
    douyin: _PlatformCaption
    tiktok: _PlatformCaption
    instagram: _PlatformCaption


SYSTEM_PROMPT = """你是一位熟悉各大社交平台风格的内容运营，帮用户给自己拍的照片/视频写发布文案。
要求：
- 只根据画面和用户给的提示写，不要编造画面里没有的具体地点、人物或事件。
- 小红书：标题 20 字以内，有吸引力；正文分段、口语化、适量 emoji，像真人分享；5-10 个话题。
- 抖音：标题 30 字以内，简短有记忆点；正文一两句；3-5 个话题。
- TikTok 和 Instagram：用 {overseas_language} 写；title 是一句简短的开头，desc 是完整文案（可以包含 title 的意思）；TikTok 3-6 个话题，Instagram 8-15 个话题。
- 话题（tags）只写词本身，不要带 # 号。"""


def load_post_file(post_dir: Path) -> dict:
    path = post_dir / POST_FILE
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 格式不对，应该是 key: value 的形式")
    return data


def _image_block(path: Path) -> dict:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def generate_with_claude(
    frames: list[Path], kind: str, hint: str, model: str, overseas_language: str
) -> dict[str, Caption] | None:
    import anthropic

    try:
        client = anthropic.Anthropic()
        content = [_image_block(p) for p in frames[:6]]
        what = "一组照片（图文帖）" if kind == "images" else "一段视频（以下是抽出的几帧画面）"
        prompt = f"这是我要发布的{what}。"
        if hint:
            prompt += f"\n我的补充说明：{hint}"
        prompt += "\n请为小红书、抖音、TikTok、Instagram 分别写标题、正文和话题。"
        content.append({"type": "text", "text": prompt})

        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT.format(overseas_language=overseas_language),
            messages=[{"role": "user", "content": content}],
            output_format=_GeneratedCaptions,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            sns_logger.warning("Claude 没有生成文案，改用默认文案")
            return None
        result = response.parsed_output
        return {p: Caption(**getattr(result, p).model_dump()) for p in PLATFORMS}
    except anthropic.AuthenticationError:
        sns_logger.warning("没有可用的 Anthropic API Key（设置 ANTHROPIC_API_KEY），跳过 AI 文案")
    except anthropic.APIConnectionError:
        sns_logger.warning("连不上 Anthropic API，跳过 AI 文案")
    except anthropic.APIStatusError as exc:
        sns_logger.warning(f"AI 文案生成失败（{exc.status_code}），改用默认文案")
    except TypeError:
        # SDK 找不到任何凭证时在构造请求阶段抛 TypeError
        sns_logger.warning("没有配置 ANTHROPIC_API_KEY，跳过 AI 文案，使用 post.yaml / 文件夹名")
    return None


def _save_generated(post_dir: Path, data: dict, generated: dict[str, Caption]) -> None:
    """把生成的文案写回 post.yaml，方便查看 / 修改，重跑时也不会变。"""
    for platform, caption in generated.items():
        section = data.setdefault(platform, {})
        section.setdefault("title", caption.title)
        section.setdefault("desc", caption.desc)
        section.setdefault("tags", caption.tags)
    (post_dir / POST_FILE).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
    )


def resolve_captions(
    post_dir: Path,
    frames: list[Path],
    kind: str,
    use_ai: bool,
    model: str,
    overseas_language: str,
) -> tuple[dict, dict[str, Caption]]:
    data = load_post_file(post_dir)
    has_any_text = any(data.get(k) for k in ("title", "desc")) or all(
        isinstance(data.get(p), dict) and data[p].get("desc") for p in PLATFORMS
    )
    if use_ai and not has_any_text:
        generated = generate_with_claude(frames, kind, str(data.get("hint", "")), model, overseas_language)
        if generated:
            _save_generated(post_dir, data, generated)
            sns_logger.success(f"AI 文案已写入 {post_dir / POST_FILE}")

    base = Caption(
        title=str(data.get("title") or post_dir.name),
        desc=str(data.get("desc") or ""),
        tags=list(data.get("tags") or []),
    )
    captions = {}
    for platform in PLATFORMS:
        override = data.get(platform) or {}
        captions[platform] = Caption(
            title=str(override.get("title") or base.title),
            desc=str(override.get("desc") or base.desc),
            tags=list(override.get("tags") or base.tags),
        ).clipped(platform)
    return data, captions
