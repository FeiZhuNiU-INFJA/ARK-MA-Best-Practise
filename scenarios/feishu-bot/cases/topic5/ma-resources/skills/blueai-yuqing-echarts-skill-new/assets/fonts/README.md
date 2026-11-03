# ECharts 中文字体

字体二进制不放源码目录、不进入 Git。`tools/prepare_fonts.py` 统一管理 Topic5 的字体来源、SHA-256、下载和缓存；`tools/pack_skills.sh` 调用它，把校验后的字体加入仓库外的临时 Skill 目录，再生成上传 MA 的 ZIP。

默认缓存目录为 `~/.cache/topic5-fonts/`。可用 `TOPIC5_FONT_CACHE_DIR` 指定另一个仓库外目录。首次或缓存损坏时下载；有效缓存可离线复用。下载、校验或注入失败都会停止打包。

| 字体 | 官方版本 | SHA-256 |
|---|---|---|
| SourceHanSansSC-Regular.otf | Adobe Source Han Sans 2.004R | `84bbd4ace91d327b3ad1a581c688196278a4e41308520176f419180064e4af2b` |
| SourceHanSansSC-Bold.otf | Adobe Source Han Sans 2.004R | `3baae4d7da5af78133c2db849814e1c0ca962e490f681714922cc499c6aeb355` |

来源为 `adobe-fonts/source-han-sans@2.004R/OTF/SimplifiedChinese/`，通过公开 jsDelivr 分发。字体未修改，许可证为 SIL Open Font License 1.1，完整原文见同目录 `LICENSE`。

MA 中仍挂载到本 Skill 的 `assets/fonts/`。`scripts/render_chart.js` 将字体提供给 Resvg，并关闭系统字体加载，避免中文缺字。运行时不下载字体。

`update_ma.sh` 在运行单测前也会按同一清单准备仓库外的临时 Skill 副本，通过 `TOPIC5_SKILLS_ROOT` 指向它们；无需手动将字体复制回源码目录。
