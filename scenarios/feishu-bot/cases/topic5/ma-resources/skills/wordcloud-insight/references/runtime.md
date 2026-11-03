# 运行依赖与边界

Python 3.10+。绘图依赖NumPy、Pillow、WordCloud、fonttools；XLSX转换依赖openpyxl。版本范围在requirements.txt。WordCloud自身可能依赖matplotlib；这里不使用matplotlib绘图。

```sh
python scripts/wordcloud_insight.py preflight
```

Topic5 适配允许 `preflight --font <路径>` 检查与 render 相同的显式字体路径，具体字符覆盖仍由 render 验证。默认不传--font，遵循客户自动优先顺序：Windows优先微软雅黑粗体，其他系统优先Noto Sans CJK粗体。适配包的assets/fonts提供官方NotoSansCJK-Bold.ttc与开放字体许可证，系统Noto粗体不可用时使用随包Noto粗体，常规字重排在粗体候选之后。仅显式配置TOPIC5_WORDCLOUD_FONT时覆盖该顺序；实际字体与哈希记入render.json。

检查不会联网或安装。缺依赖时，在已授权任务目录创建venv并安装requirements.txt，例如`python -m venv .venv`后用该环境Python执行`-m pip install -r <Skill目录>/requirements.txt`。使用者的网络/安装授权仍适用。不要改系统全局Python。

preflight的READY退出0；NEEDS_SETUP退出2，JSON保留missing/packages/fonts并给出修复动作。它检查整个工具包环境，不能代替具体命令验证；图形环境缺失只阻止相关步骤，已验证的词频分析可独立保留。失败后按[异常处理](errors.md)处理，不以有文件或退出码0推断完整交付。

客户原包不分发字体；Topic5适配包随包提供官方Noto Sans CJK粗体及其许可证。可用`--font`指定单个能覆盖全部入图字符的字体。自动搜索Windows微软雅黑粗体、Linux/macOS常见Noto Sans CJK粗体、随包Noto粗体和常规字重降级项；实际选用字体写入render.json。缺字体或缺字形时报错，避免生成方框。其他字重/系统的画面不保证逐像素等同设计稿。

默认排布继承v0.3自然填隙：全部横排，大词先放、柔和中心吸引，中小词可填主体间隙；无云朵蒙版和中心禁入圈。字号由WordCloud内置relative_scaling=auto产生，再以最小字号限制可读性。render保留RGBA透明图，同时在纯白底上合成RGB白底图，不重跑布局；两图同尺寸、同字形，半透明抗锯齿边缘按白底自然混合。配色为手选近似参考风格，不是飞书官方色板。按相对最高频次调整颜色强度，使用参考样本28/87、18/87两个比例；它们是样式参数，不是分析层级或业务规则。

先按原词频降序选定最多100个展示词（同频沿用原表顺序），再应用现有字号、配色和排布。输入≤100词时样式路径不变，包括少于50词。

当固定字号无法容纳全部选定入图词时，在显式最小字号范围内最多8次降低最大字号重新排布，记录实际字号；始终保留全部选定入图词。若仍失败，返回LAYOUT_CAPACITY，建议加大画布或由用户明确选择显示范围，不在Top100规则之外进一步静默截取或删词。不同尺寸/词长/极端分布需看图检查。

Skill不绑定某个模型API或MCP；具有本地文件、Python及图片查看能力的Agent可按文档调用。原帖抽样、全文阅读和语义判断由Agent完成，本地脚本仅校验可选阅读记录，无新增依赖或抓帖服务。包内脚本不依赖开发机路径或历史Demo文件。跨Runtime自动发现、安装后调用和全新操作系统字体差异需分别验证，不能从本机离线测试外推。
