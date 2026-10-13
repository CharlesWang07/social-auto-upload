"""素材整理：把一个帖子文件夹里的照片 / 视频转换成各平台都能直接用的文件。

规则（一个文件夹 = 一条帖子）：
- 只有照片：发图文。照片统一转成 JPG（iPhone 的 HEIC 也会转），TikTok 额外合成一段幻灯片视频。
- 只有一个视频：发视频，必要时转成 H.264 MP4（HDR 会转成 SDR，避免发出去发灰）。
- 多个视频，或照片和视频混在一起：按文件名顺序拼成一个竖屏视频。
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - 没装时 HEIC 交给 macOS sips
    register_heif_opener = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
BUILD_DIR_NAME = ".sns_build"
MAX_IMAGE_EDGE = 4096
SLIDE_SECONDS = 3
VERTICAL = (1080, 1920)


@dataclass
class PreparedMedia:
    kind: str  # "images" | "video"
    images: list[Path] = field(default_factory=list)
    video: Path | None = None
    # TikTok 网页端只能发视频，图文帖用幻灯片视频代替
    tiktok_video: Path | None = None
    preview_frames: list[Path] = field(default_factory=list)


def list_media(post_dir: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(
        p for p in post_dir.iterdir() if p.is_file() and not p.name.startswith(".")
    )
    images = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXTS]
    return images, videos


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令执行失败: {' '.join(cmd)}\n{result.stderr[-2000:]}")
    return result.stdout


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("需要 ffmpeg，请先运行 `brew install ffmpeg`")


def probe(video: Path) -> dict:
    out = _run([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(video),
    ])
    return json.loads(out)


def _video_stream(info: dict) -> dict:
    return next((s for s in info["streams"] if s.get("codec_type") == "video"), {})


def _has_audio(info: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in info["streams"])


def _is_hdr(stream: dict) -> bool:
    return stream.get("color_transfer") in {"arib-std-b67", "smpte2084"}


def convert_image(src: Path, dst: Path) -> Path:
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            img.save(dst, "JPEG", quality=92)
    except Exception:
        if platform.system() != "Darwin":
            raise
        _run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_IMAGE_EDGE), str(src), "--out", str(dst)])
    return dst


def _to_sdr_with_avconvert(src: Path, dst: Path) -> bool:
    """macOS 自带的 avconvert 能把 iPhone 的 HDR 视频正确转成 SDR H.264。"""
    if platform.system() != "Darwin" or not shutil.which("avconvert"):
        return False
    result = subprocess.run(
        ["avconvert", "--preset", "Preset1920x1080", "--source", str(src), "--output", str(dst), "--replace"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def normalize_video(src: Path, dst: Path) -> Path:
    """单个视频：已经是 H.264 SDR 就只换封装，否则重新编码。"""
    info = probe(src)
    stream = _video_stream(info)
    if _is_hdr(stream):
        tmp = dst.with_name(dst.stem + "_sdr.mov")
        if _to_sdr_with_avconvert(src, tmp):
            src, info, stream = tmp, probe(tmp), _video_stream(probe(tmp))
    if stream.get("codec_name") == "h264" and not _is_hdr(stream):
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
    else:
        _run([
            "ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-vf", "scale='if(gte(iw,ih),min(1920,iw),-2)':'if(gte(iw,ih),-2,min(1920,ih))',format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst),
        ])
    return dst


_VERTICAL_FILTER = (
    "[0:v]split[a][b];"
    "[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:5[bg];"
    "[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v]"
).format(w=VERTICAL[0], h=VERTICAL[1])


def _vertical_segment(src: Path, dst: Path, is_image: bool) -> Path:
    """把一张图或一段视频转成统一规格（1080x1920 / 30fps / 带音轨）的片段，方便无损拼接。"""
    if is_image:
        inputs = ["-loop", "1", "-t", str(SLIDE_SECONDS), "-i", str(src)]
        audio_map, duration = "1:a", ["-t", str(SLIDE_SECONDS)]
    else:
        info = probe(src)
        inputs = ["-i", str(src)]
        audio_map = "0:a:0" if _has_audio(info) else "1:a"
        duration = ["-t", info["format"]["duration"]]
    _run([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", _VERTICAL_FILTER, "-map", "[v]", "-map", audio_map, *duration,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", str(dst),
    ])
    return dst


def build_vertical_video(items: list[Path], dst: Path, work: Path) -> Path:
    segments = []
    for index, item in enumerate(items):
        seg = work / f"seg_{index:03d}.mp4"
        _vertical_segment(item, seg, is_image=item.suffix.lower() in IMAGE_EXTS)
        segments.append(seg)
    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-movflags", "+faststart", str(dst),
    ])
    for seg in segments:
        seg.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)
    return dst


def extract_frames(video: Path, work: Path, count: int = 4) -> list[Path]:
    """抽几帧给 AI 写文案用。"""
    duration = float(probe(video)["format"].get("duration", 0) or 0)
    frames = []
    for i in range(count):
        ts = duration * (i + 0.5) / count
        out = work / f"frame_{i}.jpg"
        _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=1024:-2", str(out),
        ])
        frames.append(out)
    return frames


def prepare(post_dir: Path) -> PreparedMedia:
    images, videos = list_media(post_dir)
    if not images and not videos:
        raise ValueError(f"文件夹里没有照片或视频: {post_dir}")

    work = post_dir / BUILD_DIR_NAME
    work.mkdir(exist_ok=True)

    if images and not videos:
        converted = [convert_image(img, work / f"img_{i:02d}.jpg") for i, img in enumerate(images)]
        _require_ffmpeg()
        slideshow = work / "tiktok_slideshow.mp4"
        if not slideshow.exists():
            build_vertical_video(converted, slideshow, work)
        return PreparedMedia(kind="images", images=converted, tiktok_video=slideshow, preview_frames=converted[:6])

    _require_ffmpeg()
    final = work / "video.mp4"
    if not final.exists():
        if len(videos) == 1 and not images:
            normalize_video(videos[0], final)
        else:
            # ffmpeg 读不了 HEIC，照片先转 JPG，再按原文件名顺序拼接
            items = [(v.name, v) for v in videos]
            items += [(img.name, convert_image(img, work / f"img_{i:02d}.jpg")) for i, img in enumerate(images)]
            build_vertical_video([path for _, path in sorted(items)], final, work)
    frames = extract_frames(final, work)
    return PreparedMedia(kind="video", video=final, tiktok_video=final, preview_frames=frames)
