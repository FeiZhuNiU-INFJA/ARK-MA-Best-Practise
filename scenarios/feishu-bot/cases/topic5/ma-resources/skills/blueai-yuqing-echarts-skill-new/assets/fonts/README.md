# ECharts 中文字体（本地依赖，不入 Git）

该目录需要在执行 `cases/topic5/update_ma.sh` **之前**放入以下两个文件：

```text
SourceHanSansSC-Regular.otf
SourceHanSansSC-Bold.otf
```

目标目录：

```text
scenarios/feishu-bot/cases/topic5/ma-resources/skills/
blueai-yuqing-echarts-skill-new/assets/fonts/
```

示例：

```bash
FONT_DIR=scenarios/feishu-bot/cases/topic5/ma-resources/skills/blueai-yuqing-echarts-skill-new/assets/fonts
cp /path/to/SourceHanSansSC-Regular.otf "$FONT_DIR/"
cp /path/to/SourceHanSansSC-Bold.otf "$FONT_DIR/"
```

当前验收文件的 SHA-256：

```text
84bbd4ace91d327b3ad1a581c688196278a4e41308520176f419180064e4af2b  SourceHanSansSC-Regular.otf
3baae4d7da5af78133c2db849814e1c0ca962e490f681714922cc499c6aeb355  SourceHanSansSC-Bold.otf
```

用途：`scripts/render_chart.js` 会把目录内字体作为 Buffer 传给 Resvg，并关闭系统字体
加载，以保证 MA Linux 沙箱生成的中文图表不会缺字。字体缺失时禁止打包部署，避免上线后
n14 图表静默变成方框或空白文字。

字体文件受 `.gitignore` 保护，只进入本地生成的 Skill ZIP，不进入 Git。
