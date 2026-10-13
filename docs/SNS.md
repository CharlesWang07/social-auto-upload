# sns：一个文件夹，一键发四个平台

在 social-auto-upload 的基础上加的一层：把照片 / 视频放进文件夹，运行 `sns publish`，自动发到 **抖音、小红书、TikTok、Instagram**。

## 它会做什么

1. **整理素材**（一个文件夹 = 一条帖子）
   - 只有照片：发图文。iPhone 的 HEIC 自动转 JPG，方向自动校正。TikTok 网页端只能发视频，会自动合成一段竖屏幻灯片视频（每张 3 秒）
   - 只有一个视频：发视频。HEVC / HDR 视频会转成 H.264 SDR（避免发出去发灰）
   - 多个视频，或照片和视频混着：按文件名顺序拼成一个 1080×1920 竖屏视频
2. **生成文案**：文件夹里没有 `post.yaml` 时，用 Claude 看图，为每个平台写标题、正文和话题，并写回 `post.yaml`（可以改了再发）
3. **依次发布**：一个平台失败不影响其他平台。进度记录在 `.sns_state.json`，重新运行只补发失败的平台
4. **归档**：全部平台发完后，把文件夹移到 `已发布/`

## 安装（macOS）

```bash
brew install ffmpeg
cd social-auto-upload
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/patchright install chromium
cp conf.example.py conf.py
cp sns_config.example.yaml sns_config.yaml
```

AI 文案需要 Anthropic API Key（不设置也能用，只是会用文件夹名当标题）：

```bash
echo 'export ANTHROPIC_API_KEY=你的key' >> ~/.zshrc
```

## 首次登录

```bash
.venv/bin/sns login all
```

- 抖音、小红书：扫二维码
- TikTok、Instagram：会弹出浏览器窗口，正常登录即可，检测到登录成功会自动保存

登录态保存在 `cookies/` 目录下（已加入 .gitignore，不会被提交）。之后可以用 `sns check` 检查有没有过期。

## 日常使用

```text
~/Desktop/SNS上传/待发布/
├── 镰仓一日游/
│   ├── IMG_0001.HEIC
│   ├── IMG_0002.HEIC
│   └── post.yaml        ← 可选
└── 猫咪视频/
    └── IMG_0010.MOV
```

```bash
.venv/bin/sns publish              # 发布收件箱里的所有帖子
.venv/bin/sns publish --dry-run    # 先看看素材和文案，不发布
.venv/bin/sns publish 某个文件夹     # 只发一条
.venv/bin/sns publish --platforms douyin xiaohongshu
.venv/bin/sns publish --headed     # 显示浏览器，排查问题时用
```

### post.yaml（全部可选）

```yaml
hint: 周末和朋友去镰仓，看海、吃了吻仔鱼盖饭   # 给 AI 的提示
title: 镰仓一日游
desc: 海风好舒服
tags: [镰仓, 日本旅行]
platforms: [douyin, xiaohongshu]             # 只发这几个平台
schedule: "2026-10-20 20:00"                 # 定时发布，仅抖音 / 小红书支持，需晚于当前 2 小时
tiktok:                                      # 单独覆盖某个平台
  desc: Weekend in Kamakura
  tags: [kamakura, japan]
```

只写 `hint` 时，AI 会参考它来写文案；写了 `title` 或 `desc` 就不会调用 AI。

## 已知限制

- 抖音、小红书、TikTok、Instagram 都是通过模拟浏览器操作发布的。平台改版后选择器可能失效，失败时截图保存在 `logs/screens/`
- 频繁自动发布可能触发平台风控，建议一天几条以内
- 抖音发布时偶尔会要求短信验证码，见 [CLI.md](CLI.md) 的说明
- TikTok 图文帖以幻灯片视频的形式发布
