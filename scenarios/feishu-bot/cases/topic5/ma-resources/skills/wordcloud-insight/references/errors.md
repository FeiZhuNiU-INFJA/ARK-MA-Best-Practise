# 异常返回与停止规则

适用于所有Agent；无OpenAI API或特定平台依赖。运行前按contracts.md校验关键输入；出现异常或部分完成时读本文件。正常成功不追加“分析说明”。

## 命令返回契约

脚本正常启动后，每次业务命令stdout返回一个JSON对象；第三方诊断可在stderr。`--help`是唯一正常文本输出，退出0。Python未启动、语法/包损坏导致无法加载、进程被强杀或输出管道不可写时不能保证JSON，调用方须把非JSON/进程异常视为执行失败，不猜成功。

| status | 退出码 | 含义与处理 |
| --- | --- | --- |
| READY / VALID / PREPARED / RENDERED / COMPOSED | 0 | 仅对应命令完成；仍须检查任务所需产物和语义/视觉质量 |
| ERROR | 2 | 当前命令失败，停止依赖该结果的步骤 |
| NEEDS_SETUP | 2 | preflight发现依赖或字体未就绪；先修复受影响环境 |
| PARTIAL | 3 | compose已经生成文件，但limitations说明本次目标未全部完成 |

ERROR、NEEDS_SETUP、PARTIAL均包含`code`（稳定类别）、`stage`（arguments/preflight/prepare/validate/render/compose）、`message`（本次具体原因）、`retryable`和`suggested_action`。`retryable:false`表示原样自动重试无意义；修正输入、配置或代码后可重试，不表示永久不可恢复。调用方优先按code处理，不能解析message措辞作逻辑判断。

```json
{"status":"ERROR","code":"INVALID_VALUE","stage":"validate","message":"Invalid frequency for 炸鸡","retryable":false,"suggested_action":"按输入契约修正指出的字段或词频；保留原件，不自行猜值、删词或合并重复词。"}
```

NEEDS_SETUP另含missing/packages/fonts；PARTIAL另含output/topics/completed_artifacts。completed_artifacts只确认生成了这些文件，不证明所有分析目标已实现。compose只要有非空limitations即返回PARTIAL；不能通过删除真实未完成说明改成成功。

## 异常清单

| code | 修复或处理 |
| --- | --- |
| CLI_ARGUMENT | 依据--help修正子命令、必填参数与参数类型 |
| READ_JSON | 检查路径、权限、UTF-8编码、JSON语法 |
| INPUT_SCHEMA / INPUT_CONTEXT / INVALID_TEXT | 修正结构或指出的必填字段；未知可选背景不编造 |
| INPUT_SENTIMENT / INPUT_CONTENT_TYPE / INPUT_METRIC | 使用已提供的筛选范围与合法枚举，不猜情感或口径 |
| INPUT_WORDS / INVALID_TERM / DUPLICATE_TERM / INVALID_VALUE / ZERO_FREQUENCY | 修复空表、非法词项、重复词、非有限/非正/越界词频；不自行删词或合并 |
| TABLE_FORMAT / EMPTY_TABLE / TABLE_ROW / TABLE_VALUE | 回查表格式、表头及报错行 |
| AMBIGUOUS_SHEET / INVALID_SHEET / AMBIGUOUS_COLUMN | 显式指定原表中的正确sheet及唯一词/数值列 |
| OUTPUT_EXISTS | 使用新输出路径，保留已有文件；compose接续对应render目录 |
| ENVIRONMENT_NOT_READY / DEPENDENCY_MISSING | 修复授权任务环境的依赖或字体，再预检，不改全局Python |
| FONT_MISSING / INVALID_GLYPH / UNSUPPORTED_GLYPH | 指定可读、覆盖全部入图词的字体，不用方框或删词替代 |
| CANVAS_SIZE / FONT_SIZE / LAYOUT_CAPACITY | 在授权范围调整画布/字号；选定入图词仍须完整保留 |
| CLIPPED_GLYPH / MASK_OVERFLOW / GLYPH_OVERLAP / DATA_MISMATCH | 拒绝交付词云；检查渲染实现，不跳过完整性检查 |
| INPUT_MISMATCH / IMAGE_MISMATCH | 核对input、render记录及透明/白底双图；缺失、改动或白底合成不一致时新目录重绘 |
| TOPIC_SCHEMA / EMPTY_ANALYSIS / TOPIC_TERMS / UNKNOWN_TERM | 修正议题结构/原词引用，不编造洞察以通过检查 |
| POST_EVIDENCE / POST_EVIDENCE_REQUIRED | 回查原帖、字段定位、摘录、阅读记录；不能静默删证据 |
| EXECUTION_ERROR | 检查报错文件、权限、格式或参数；修复后重试 |
| UNEXPECTED_ERROR | 保留错误类型与触发步骤交由维护者检查，不循环重试 |
| INTERRUPTED | 检查中断阶段与残留文件；继续时使用新输出路径 |
| OBJECTIVE_INCOMPLETE | 保留已验证成果，说明未完成目标与所需补充 |

## 任务层面的停止、降级与交付

- 分析对象、词表合法性或两者归属不清：停止该输入的分析和配图，说明具体字段/问题及所需修正。不用示例数据代替，不将空结果当成功。
- 未提供可选原帖：正常走词频分析，不视为异常，不额外说明。背景/时间等允许未知的字段依contracts.md处理，不能一概升级为阻塞。
- 已提供原帖但无法读取、定位或验证：先修复或补充可读资料。无法修复时停止依赖该原帖的判断；仅保留独立证据支持的内容，并明确“原帖辅助分析未完成”、原因和所需补充。若明确要求必须结合原帖，该部分不能按完整目标交付。脚本不自动移除引用或选择降级，调用Agent负责重写并重新核验相关判断。
- 配图失败但词频及分析依据均已核验：可独立保存analysis.md；明确哪张PNG未完成，整体为部分完成。compose依赖有效透明及白底双PNG，不用占位图或伪造render记录凑预览。反之，分析失败时可保留已核验PNG，明确分析未完成。
- 命令失败时，保留此前已验证成果；失败阶段残留文件即使存在也不算成功产物。未声明原子提交：写入中断可能留下文件，不能直接覆盖或用残留文件继续冒充成功；在新输出目录重试，旧文件保留供排查。
- 修正了input必须重新validate并重绘；只修正topics/阅读证据时可复用匹配且完整的透明及白底双PNG/render记录。已有分析文件时另建目录，复制并核验双图及记录后再compose，不覆盖历史。
- 对用户只说明受影响目标、具体原因、已完成内容和下一步补充；内部错误细节留在任务记录，不把通用限制塞入成功报告。脚本成功不代表Agent已完成语义检查，任务整体状态由调用Agent依据用户目标判断。
