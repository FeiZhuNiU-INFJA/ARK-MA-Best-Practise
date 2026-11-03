# 输入输出与命令

所有命令相对Skill目录运行；工作数据和输出放用户任务目录，不放Skill内部。路径带空格时使用所在Shell的正确引号。

## input.json

```json
{
  "schema_version": 1,
  "context": {
    "subject": "分析对象",
    "project": "项目名称或未提供",
    "objective": "希望解决的业务问题或未提供",
    "background": "项目背景、传播动作、卖点与相关已知事实；未提供可空",
    "period": "数据时间范围或未知",
    "platform": "单个平台，或用户明确提供的组合范围",
    "sentiment": "全部",
    "content_type": "UGC"
  },
  "metric": "post_count",
  "words": [{"term": "词项", "value": 67}]
}
```

context必须有subject、period、platform、sentiment、content_type，均为非空字符串；已知信息如实填写，缺失时间/平台可显式写“未知”。sentiment允许全部/正面/负面/中性/未知；content_type允许全部/PGC/UGC/未知。metric为`occurrence_count`（出现次数）、`post_count`（出现帖子数）、`unknown`（未确认）。可选context字符串project/objective/background。

words含1–500个唯一原词，每项term非空、最多200字符，value为有限正数（≤10^15）。零值返回`ZERO_FREQUENCY`，由使用者明确处理后再绘制；负值/重复原词/空表返回输入错误，不自动合并或丢行。正数小数保留数值，若为加权统计，应在背景说明权重。表格里的未知权重不能冒称帖子数。

分析始终读取完整words；配图≤100词全显，>100词按原词频降序取前100，同频保留原表顺序。prepare/validate不截词，未入图词仍可作为议题代表词。

## CSV/XLSX转换

把不含words的上述JSON保存为context.json，然后：

```sh
python scripts/wordcloud_insight.py prepare --table words.xlsx --context context.json --output input.json
python scripts/wordcloud_insight.py validate --input input.json
```

默认识别词项列`关键词/词项/词/term/word`，数值列`频次/词频/声量/value/count/frequency`；列歧义时用`--term-column`和`--value-column`明确指定。XLSX多工作表必须用`--sheet`选定，不猜测；只读缓存值，不执行公式。CSV为UTF-8（可BOM）；原词文本不strip或改写。JSON也可由Agent直接整理，避免逐行手抄大表。

## 可选原帖输入

本地原帖表或文本可随词频表提供，无需改动input.json或重算词频。由Agent读取、标准化为posts.json并生成post-review.json，抽样规则见[原帖流程](posts.md)，字段格式与定位见[证据契约](post-evidence.md)。新任务使用原帖/阅读记录schema2。有阅读记录时compose追加`--post-review post-review.json`；无原帖无需此参数。

## topics.json

```json
{
  "summary": "可选总览，省略也可",
  "topics": [
    {
      "title": "议题标题",
      "terms": ["词项"],
      "interpretation": "连贯的分析文字，可含有依据的策略解释与建议。",
      "rationale": "内部证据与判断依据，不进入业务正文",
      "post_ids": []
    }
  ],
  "limitations": ["仅在本次明确目标无法完成时，说明未完成项、原因及所需补充"]
}
```

post_ids为可选原帖ID列表；仅引用post-review.json中实际读过且范围已核查的记录，未传--post-review时不得填写非空post_ids。

title/interpretation/rationale为非空字符串；terms为原词字符串列表，脚本取回数值。最多5个议题；0议题时必须提供limitations解释原因。不要把数值或新造的同义词放入terms。summary可省略；正常完成时省略limitations或置为空数组。仅本次明确目标无法完成时填写limitations；通用数据限制留在内部记录，与整体报告拉齐。

```sh
python scripts/wordcloud_insight.py render --input input.json --out output-01
python scripts/wordcloud_insight.py compose --input input.json --topics topics.json --out output-01
```

render支持`--font /path/to/bold.ttf`、`--width 1400 --height 880`、`--max-font-size 128 --min-font-size 10`。允许画布320–2400宽、240–1600高，尺寸影响可容纳数量。输出目录必须新建，避免覆盖历史；compose使用同目录并拒绝覆盖已存在的分析文件。

v0.2.4的compose要求两张图片及对应哈希，检查白底图为RGB且与透明图白底合成逐像素一致；缺图、改图或不一致返回IMAGE_MISMATCH/退出2。旧版只有透明图的运行记录需在新目录重新render，不自动改写历史结果。

v0.2.6的compose另校验render.json中的selection和逐词layout与完整输入计算出的Top100集合一致；缺少selection的旧运行需在新目录重新render，不改写历史。

## 产物

- `analysis.md`：分析范围、可选总览、议题/代表词及数值/解读；仅目标未完成时附具体原因。
- `wordcloud.png`：RGBA透明图，无背景底板；字形边缘保留半透明抗锯齿。
- `wordcloud-white.png`：RGB不透明PNG，将同一次透明渲染逐像素合成到纯白色（#FFFFFF）；尺寸、词项、字号和排布一致，不另行渲染或改色。render默认生成两图，无需额外参数。
- `preview.html`：超过100词时注明展示前100项及完整输入词数，分析正文不增加常规说明。单文件离线组合预览，白底/深底/棋盘切换只影响预览；“保存透明PNG”和“保存白底PNG”分别下载对应文件，与当前预览底色无关。
- `render.json`：输入与双PNG哈希（png_sha256、white_png_sha256）、白底颜色及模式、版本/字体/实际参数、逐词位置/字号/颜色、数据及重叠检查。selection包含policy、limit、tie_break、input_count、displayed_count和omitted_words（原词/原值）；all_words_preserved仅在全量入图时为true，all_display_words_preserved表示选定集合完整绘制。
- `analysis-evidence.json`：输入指纹、内部rationale和数值校验状态；使用原帖时含post_review阅读记录、来源指纹与议题post_ids。语义复核由Agent执行，脚本不宣称策略结论已被验证。

错误以JSON输出、退出码2；环境未就绪同样退出2。compose含未完成目标说明时返回PARTIAL、退出3，不当作完整交付。状态字段、异常清单与停止/降级规则见[异常处理](errors.md)。无网络请求，不自动重试外部服务。
