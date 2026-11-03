# Noto Sans CJK Bold

字体二进制不进入源码仓库。`tools/prepare_fonts.py` 与 ECharts 字体共用统一清单、下载和校验流程；`tools/pack_skills.sh` 调用它，将官方 `NotoSansCJK-Bold.ttc` 缓存到仓库外的 `~/.cache/topic5-fonts/`，校验 SHA-256 后加入临时打包目录的 `assets/fonts/`。缓存命中且校验通过时复用；下载或校验失败则停止打包。`TOPIC5_FONT_CACHE_DIR` 可指定其他仓库外缓存目录。

部署包仍提供该字体，让 MA Linux 沙箱按客户要求使用 Noto Sans CJK 粗体，运行时无需下载。本地运行可通过 `--font` / `TOPIC5_WORDCLOUD_FONT` 指向外部缓存中的字体。

来源：notofonts/noto-cjk 官方仓库，`Sans/OTC/NotoSansCJK-Bold.ttc`。
公开分发地址：https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTC/NotoSansCJK-Bold.ttc

许可证为 SIL Open Font License 1.1，完整原文见同目录 `LICENSE`。字体未修改。

随包文件SHA-256：`faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb`。

Windows 优先系统微软雅黑粗体；其他系统优先系统 Noto Sans CJK 粗体，随后使用本目录 Noto 粗体。常规字重仅在粗体候选不可用或无法覆盖入图字符时尝试。显式 `--font` / `TOPIC5_WORDCLOUD_FONT` 可覆盖自动选择。
