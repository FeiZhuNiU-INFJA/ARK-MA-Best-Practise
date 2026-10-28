---
name: blueai-yuqing-echarts-skill-new
version: 1.5.1-local
description: Topic 2 舆情报告 与 Topic 5 营销活动复盘报告 专用 ECharts 图表 skill（本地安装版）。渲染架构：ECharts SSR + @resvg/resvg-js，零 Chrome/浏览器依赖。Topic 2 S4 pipeline 和手动调用、Topic 5 全部图表类型统一走此版本。
---

# BlueAI 舆情 ECharts Skill（MA 迁移版 v1.5.1-local）

> **渲染架构**：ECharts 5 SSR → SVG → @resvg/resvg-js → PNG，**零浏览器依赖**
> **MA 挂载路径**：`/mnt/skills/blueai-yuqing-echarts-skill-new/`

## MA 安装状态

- `node_modules` 不入 skill 包；Environment `setup_script` 在沙箱中安装
  ECharts 5.6.0 与 `@resvg/resvg-js` 2.6.2，安装失败时由上层 handler 降级
- 字体随部署包提供于 `assets/fonts/SourceHanSansSC-Regular.otf` 与
  `assets/fonts/SourceHanSansSC-Bold.otf`
- 不需要 `libatk1.0-0`、`libgbm1` 等 Chrome 系统依赖

## CHANGELOG

### v1.5.1-local (2026-08-12)
- frontmatter description 补充 Topic 5（营销活动复盘报告）引用，与 `skill-routing.md` 规则 #3、`KS_Feishu_Format.md` §10.1 保持一致
- 无代码/渲染逻辑变更，仅文档修正

### v1.5.0-local (2026-05-28)
- 从 TOS v1.5.0 复制并本地安装 node_modules
- 渲染引擎：Puppeteer（旧） → ECharts SSR + @resvg（无浏览器依赖）
- 解决 S4 非词云图表全部渲染失败问题（8/8 全部通过测试）
- ChartRenderer（chart_builder.py）已更新指向此版本

---

# BlueAI 舆情 ECharts Skill

本 skill 用于舆情报告图表生成。目标不是“把所有表都画出来”，而是“只生成值得进入报告的图表”。

## 1. 适用边界

适合：
- 舆情报告中的统计图表
- 情感分布、平台分布、负面分布
- 时间趋势图
- 讨论主体 × 情感构成图
- 关键词频次排名图
- PGC / UGC 结构对比图

不适合直接出图：
- 单值 KPI 汇总
- 说明性文本、观点示例、占位章节
- 没有比较维度的数据
- 数据点极少且不足以支持图形表达的数据

这类内容优先：
- 转卡片
- 转表格
- 转说明文字

## 2. 图表规则来源

迁移包不包含原工程的维护参考文件。图表资格由
`pipeline-orchestrator/charts.py::build_chart_specs` 确定性判断；支持的图型和字段契约以
`scripts/render_from_spec.js` 为准。

## 3. 核心流程

标准链路：

```text
原始数据
-> 图表资格判断
-> 图型匹配
-> chart_bundle.json
-> render_from_spec.js
-> render_chart.js / 代码输出
-> 终检
-> 本地 PNG（由交付层上传飞书 media）
```

执行约束：
- 所有校验必须基于真实数据比对，禁止凭直觉或表面相似性跳过
- 数据文件中带 `图表占位` 标记的，默认为硬性需求，跳过必须给出数据级理由
- 多张图时，先做资格判断，再做映射表和去重，不得直接堆图
- 必须区分”源表原生图”与”跨表派生图”
- 派生图不得继承源章节里的 `图表占位`
- 标签策略先看图型语义，再看密度修正
- 密度只能决定是否降级显示，不能推翻图型本身的读图需求

## 4. 两条输出路径

### 路径 A：图片输出

适用：
- Markdown / Word / 飞书文档嵌入
- 需要 PNG

执行：

```bash
node scripts/render_from_spec.js --bundle "/tmp/chart_bundle.json" --chart-id "chart-01" --output "/tmp/chart.png"
node scripts/render_chart.js --js "/tmp/chart.js" --output "/tmp/chart.png" --width 1200 --height 600
```

### 路径 B：代码输出

适用：
- HTML 报告嵌入
- 前端页面接入
- 用户只要 option / JS

执行：

```bash
node scripts/render_from_spec.js --spec "/tmp/chart_spec.json" --js-out "/tmp/chart.js"
```

## 5. 当前脚本

分享版只保留运行所需脚本，评测样例和维护文档已裁剪。

## 6. 输出契约

当前中间层使用 `chart_bundle.json`，最少需要：
- `chart_id`
- `title`
- `chart_type`
- `metric`
- `dimension`
- `categories`
- `series`
- `source_row_ids`

建议补充：
- `sample_sizes`
- `source_mode`
- `warnings`
- `label_density`
- `label_policy`

## 7. 默认执行顺序

生成多张图时，按以下顺序执行：

1. 识别哪些 section 有资格出图
2. 删掉单值汇总、占位表、文本表
3. 建立 `chart_bundle.json`
4. 用 `render_from_spec.js` 生成代码或图片
5. 做终检
6. 最后渲染 PNG 或上传

如果在终检阶段才发现图本身不该存在，回到第 1 步重做，不要继续修样式。

## 8. 终检清单

在渲染完成前，至少检查：
- 是否存在空图、全零图
- 是否存在大量 0 值主导
- 是否存在极小样本类别误导
- 图型是否与数据类型匹配
- 是否有重复表达同一事实的低信息图
- 标签是否与标题、图例、轴标签、图形主体重叠

若发现上述问题，先删图或降级，不要继续修样式。

## 9. 修改后回归验证

分享版不包含维护文档；如需回归验证，请回到完整工程执行。

## 10. 维护规则

分享版仅用于运行和交付，不包含维护附录。
