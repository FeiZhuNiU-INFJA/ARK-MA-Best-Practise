# topic6 · 变更日志

记录影响 Topic6 运行流程、交付行为和方舟 Managed Agents 适配的变更。纯文案润色不进。

每条至少包含:
- 触发现象(报错/异常表现)
- 根因(为什么这个约束存在)
- 影响文件 & 参考出处

按日期倒序。

---

## 2026-09-30

### Demo 第二批恢复六路并发

- **触发现象**：`run_demo_routes.py` 虽使用线程池，但默认只有 2 个 worker，且按
  R1 的全部 chunk、R2 的全部 chunk依次提交，运行效果接近逐路推进，不符合
  R1~R5 与 C2 六路并发的流程定义。
- **调整**：改为 5 个 route worker 并发执行 R1~R5；每个 route 内仍按 5 条一批
  串行处理并逐批 checkpoint。Coordinator 同时启动 C2，因此共六路并发。
- **边界**：本次仅更新本地资源，不影响已创建的 Session；需后续执行
  `update_ma.sh` 并新建 Session 才会生效。

### 运行模式重命名

- **触发现象**：原 `full` 实际会跳过 500 条采样校准，名称容易被理解为客户完整流程；
  原 `test` 才是包含 HC1、全量重跑和 HC2 的完整模式。
- **调整**：三种模式统一为 `demo`、`full`、`skip_sampling`。`full` 对应原 `test`，
  `skip_sampling` 对应原 `full`；全量阶段产物使用
  `wide_table_skip_sampling_r{N}.xlsx`，避免与 `full` 的 500 条校准产物重名。
- **影响范围**：Gateway 触发词、Coordinator/子 Agent Prompt、C/D/E/F 脚本参数、
  HC 文案、流水线图和调试文档同步更新。

### 活跃任务用户 Token 保活

- **触发现象**：Session `sesn-20260930023459-r9zoi` 从 10:34 运行至 12:39；Phase G
  已完成，但 Phase H 调用 `lark-cli apps +list --as user` 返回
  `99991677 token_expired`，最终因没有妙搭 `online_url` 被 Gateway 标记为 stopped。
- **根因**：Gateway 原先只在创建 Session 前检查并刷新一次用户 Token。长任务超过约
  2 小时后，已挂用户 Vault 中的短期 access token 过期。
- **修复**：Gateway 为每个活跃 Job 启动保活协程，每分钟检查一次，覆盖 `running` 与
  `wait_hc`；在过期前 15 分钟使用 refresh token 原地更新同一个 Vault Credential。
  刷新失败会记录日志并继续重试，任务终态或 `/new` 取消后停止保活。
- **身份稳定性**：全程保持原 `session_id`、Vault ID 和 Credential ID，不动态追加
  Vault。方舟运行时周期性重新解析已挂 Vault 的凭据轮换。

### Phase E 流式有限并发与版块级恢复

- **触发现象**：Session `sesn-20260930023459-r9zoi` 中，旧 `pipeline_e.py` 使用非流式
  四路并发，E1~E4 同时出现 `Connection error`；用相同 Prompt 单独流式调用 E1 可成功。
- **调用优化**：E1~E4 改为默认最多 2 路并发的流式响应；断连、429 和 5xx 最多尝试
  5 次，指数退避且每次创建全新 `AsyncOpenAI` 客户端，避免复用失效连接。
- **可靠恢复**：每个成功版块立即原子写入 Markdown 和
  `pipeline_e_checkpoint_v{N}.json`。相同输入、模型、Prompt 和版本重跑时只补失败版块，
  已完成版块不重复调用或计费；显式 `--sections` 仍表示强制重跑指定版块。
- **编排约束**：Coordinator 只可原参数重跑正式入口一次，禁止 inline Python、拆成
  四次调用、改写 Prompt、切模型、提高并发或删除 checkpoint。
- **生效方式**：执行 `update_ma.sh` 更新 Skill 和 Coordinator，重启 Gateway 后创建
  新 Session；已有 Session 不会自动获得更新后的资源快照。

### Demo R1~R5 小批次恢复与失败收口

- **触发现象**：Session `sesn-20260930023459-r9zoi` 中，R1~R5 直接 Ark 批量请求出现
  `Remote end closed connection without response`。旧入口五路各发一个长请求，且要
  等五路全部成功后才统一落盘；任一路失败会丢失其余已成功结果。
- **执行优化**：`run_demo_routes.py` 保持原 R1~R5 Prompt 和输出契约，改为默认每批
  5 行、最多 2 请求并发。断连、超时、429 和 5xx 最多重试 5 次，使用指数退避与抖动。
- **传输层修正**：Session `sesn-20260930045929-siwtz` 证明仅创建新的
  `urllib.Request` 不会隔离底层失效连接，R1 第二个 chunk 起仍持续断连。正式入口改用
  `httpx`，每次 attempt 都创建并关闭独立 Client，禁用 keep-alive 与 HTTP/2；
  Environment 显式预装 `httpx>=0.27`。
- **可靠恢复**：每个成功 chunk 立即原子写 JSON checkpoint；每个 route 完成后立即写
  raw/postprocess/completion_meta、run_config 和成本，不再等待其他 route。重跑按输入、
  模型、Prompt、run_id 和 batch size 只补缺失 chunk，已完成 route 直接复用。
- **编排约束**：Coordinator 失败后只可按原参数重跑正式入口一次，禁止 inline Python、
  改写 Prompt、切模型、修改 batch size 或删除 checkpoint 来绕过问题。

### 妙搭用户 OAuth 预授权与每用户 Vault

- **根因**：MA Session 创建后不能追加 Vault；Gateway 主机上的 lark-cli 登录态也不会
  自动进入方舟沙箱。仅在 Agent 内提示 `auth login` 会让 Phase H 等到流水线末尾才失败。
- **授权入口**：新增 `authorize_miaoda_user.py`。Device Flow 完成后通过用户信息接口
  自动取得扫码者 `open_id`，不要求手工传 ID；可选 `--expected-open-id` 仅用于防止
  扫错账号，校验失败时不写凭据。
- **凭据边界**：短期 `LARKSUITE_CLI_USER_ACCESS_TOKEN` 写入每用户独立 Ark Vault；
  refresh token 只保存在权限为 `0600` 的 Topic6 SQLite 中，不进入 MA 沙箱或日志。
- **运行时接入**：Gateway 在创建任务前按消息发送者查授权并刷新短 token，再把用户
  Vault 传给新 Session。未授权或授权失效时直接拒绝启动并提示运行授权脚本。
- **权限事实**：授权范围限定为 `offline_access`、`auth:user.id:read`、
  `spark:app:read`、`spark:app:write`，覆盖身份识别、续期和妙搭发布。

## 2026-09-29

### Phase H 终态校验与报告链接修复

- **轨迹证据**：Session `sesn-20260929132115-ol7cr` 共 412 个事件。Phase G 的 HTML
  snapshot 上传因缺少 API Key 失败，Agent 将 `status.h.completed=false` 写入配置后仍
  宣称流程完成；Gateway 又从自由文本中抓取首个飞书文档 URL，并连同 JSON 尾部的
  `",` 标点写入卡片，飞书打开时进一步附加一次性登录参数。
- **终态收紧**：Gateway 不再抓取任意 URL，只接受 Phase H 结构化结果中的
  `online_url`，并校验为 HTTPS `*.aiforce.cloud`；否则任务进入 `stopped`，不显示
  “打开报告”完成按钮。
- **编排收口**：Phase G 只负责构建、校验和落盘 HTML，禁止把 snapshot 上传视为妙搭
  发布。补充迁入客户后续提供的 `miaoda-web-publish` v1.0.1，作为第 6 个 Coordinator
  Skill；按其用户 OAuth + Git 管理发布契约执行，不引入 `MIAODA_TOKEN`。OAuth 未完成
  或发布失败时不得宣称完成。
- **验证**：完整测试集 `423 passed`；6 个 Skill 已上传并重建 Coordinator。

### Demo 第二批与洞察阶段提速

- **轨迹证据**：线上 Session `sesn-20260929115416-cgepu` 共 315 个事件。
  R1~R5 只处理 14 行仍各耗时 10.0~11.6 分钟；完整 C2 与其并发但耗时约
  12.6 分钟，成为 Phase C 第二批关键路径。E1~E4 子线程耗时 2.4~5.3 分钟，
  且每个子线程都重复调用一次 `pipeline_e.py` 和全量统计预处理。
- **R1~R5 demo 快速入口**：新增 `run_demo_routes.py`，复用正式 R1~R5 Prompt，
  将每路 14 次逐行 DataHub 推理改为每路一次 Ark 批量推理，五路并发并产出原有
  raw/postprocess/completion_meta 文件与 run_config 状态。test/full 继续走 DataHub。
- **C2 demo 快速入口**：`run_topic6_c2.py --demo-fast` 用一次批量模型调用完成小样本
  事件归并，直接产出原契约 `row_id + 一级事件名`；完整 00→x4 链仍用于 test/full。
- **Phase E 去重**：整轮改为只调用一次 `pipeline_e.py`，复用其内部 E1~E4 并发，
  避免四次 `run_stats.py`、四个子 Agent 冷启动和重复自检。
- **附带修复**：修正 C2 四平台并发时 `_run_command` 参数重复传递，并统一 Coordinator
  读取真实状态路径 `C2_事件归档/c2_run/c2_status.json`。

### 单任务单卡片与 Phase F 发布契约修复

- **卡片合并**：进度与 HC1/HC2/HC3 审核态复用同一条飞书消息；HC 到来时 patch
  主卡，回调处理后继续把该卡更新为运行或终态。按 job 串行 card patch，避免 SSE
  进度与审核回调相互覆盖；主卡初始化失败时才降级补发一张。审核通过或提交备注后
  会在启动下一段 SSE 前立即恢复运行卡和已有工具进度，不等待下一条 progress 事件。
- **卡片协议统一**：运行卡此前使用 schema v1，审核卡使用 schema v2；进入审核态后
  再恢复运行卡会被飞书以 `230099 / schemaV2 card can not change schemaV1` 拒绝。
  现已将运行、等待审核和最终状态全部统一为 schema v2，保证同一消息可双向切换状态。
- **审核记录保留**：HC 回调除审核结果、备注和时间外，新增持久化审核人显示名；恢复
  运行后在独立“审核记录”区展示 `HC1 · ✅ 通过 · 审核人 · 时间`，后续 HC2/HC3
  按发生顺序追加，不占用最近工具调用行数。旧记录因历史上未保存审核人会明确显示
  “审核人未记录”，不做不可靠推断。
- **Phase F 目录与身份**：统一由应用身份动态创建报告目录，不再依赖
  `FEISHU_HOTREPORT_FOLDER_TOKEN`；操作人改读 Gateway 注入的
  `FEISHU_USER_OPEN_ID`，沙箱不再依赖 `CC_SESSION_KEY` 自行发送重复通知。
- **URL 与结果判断**：文档 URL 直接读取 `drive +import` 响应，不再硬编码租户域名；
  同时强制检查 JSON `ok == true`，避免把“退出码为 0 的业务失败”误判为成功。
- **权限与凭证安全**：补充 `docs:document.media:upload`、
  `docs:document:import` 权限要求；Coordinator 禁止枚举环境或打印 Secret、Token、
  API Key，避免敏感值进入 Session 轨迹。
- **文档可访问性**：补充 `docs:permission.member:create`、
  `docs:permission.member:transfer`、`docs:permission.member:retrieve`。Phase F 只允许
  Bot 身份发布，禁止缺 scope 时降级 Device Flow；导入后先给任务发起人 `edit`，再转移
  owner，并逐项校验授权 JSON，任一步失败均不得进入 HC3。
- **目录响应解析**：`drive +create-folder` 的 token 位于 `data.folder_token`；旧 Prompt
  误读 `data.token`，导致目录创建成功后仍被判空并触发临场 OAuth 降级，现已修正。
- **验证**：Topic6 / Gateway 完整测试集 `413 passed`。

### C2 单入口、并发状态与 C0 成本优化

- **触发现象**：最新轨迹中 C2 占 71 次模型请求里的 48 次、产生 47 次 bash 调度，
  且运行二十多分钟仍未完成；原文档还把 01~04 放在 x0 前按平台执行，与
  `x0_merge_platforms.py` 实际只合并 `clean_titles.jsonl` 的契约冲突。
- **C2 拓扑与入口**：新增 `run_topic6_c2.py`，固定为四平台并行 00 → x0 →
  merged 目录统一执行 01~07 → x2 → x3 → x4，并直接产出
  `c2_event_result_r{N}.xlsx`。Coordinator 只启动一次后台入口，不再临场编排十多个命令。
- **可靠恢复**：入口用 `c2_status.json` 记录阶段；输入、Chat 模型、Embedding 模型和
  x2 策略组成运行签名。签名不变时续跑，变化时清理旧阶段产物重跑；项目级进程锁阻止
  重复 runner 并发写缓存。
- **真正并发**：R1~R5 五个委派发出后立即后台启动 C2，六路同时运行；不再等待五路
  DataHub 任务结束后才开始 C2。
- **模型隔离**：Environment 分设 `DATAHUB_MODEL_ID=Doubao-Seed-Evolving`、
  `C2_CHAT_MODEL_ID=doubao-seed-evolving` 和
  `EMBEDDING_MODEL_ID=doubao-embedding-vision-251215`，避免跨 API 混用模型 ID。
- **状态一致性**：新增 `run_config_state.py`，通过文件锁、YAML 深合并和
  `os.replace` 原子更新并发任务状态；DataHub、C0 合并/筛选、C2 和宽表合并均写入成功
  或失败状态，不再由多个 Agent 直接覆盖 `run_config.yaml`。
- **C0 Prompt**：活跃版升级为 v5，在保留 9 字段输出契约和关键边界的前提下，从
  44,649 字符压缩到 11,263 字符（减少 74.8%）。该项只完成静态契约校验，仍需用真实
  DataHub 结果在 HC1 与 v4 对比后确认质量。
- **验证**：Topic6 聚焦测试 `20 passed`，加入 Gateway 全局单任务测试后仓库全量
  `409 passed`。

### HC3 状态一致性与轨迹问题收口

- **Gateway 全局单任务**：此前只按 `(chat_id, thread_id, user_open_id)` 防止同一用户重复
  触发，不同用户仍会并行创建 Session。现在整个 Topic6 Gateway 只允许一个
  `running` / `wait_hc` Job；新触发在创建方舟 Session 前即被拒绝，并提示等待前一任务
  结束。启动检查使用异步锁串行化，避免同时到达的消息穿透检查。
- **HC3 卡片错显 HC1**：Gateway 先把数据库阶段更新为 HC3，但发送审核卡时复用了更新前的 Job 快照。审核卡现直接以 `hc_kind` 渲染阶段，Runner 在写库后也会重新读取 Job。
- **备注补充消息未续跑**：点击“备注”后再 @bot 的正文此前会落入默认帮助回复。Gateway 现优先识别等待补充说明的 HC，将正文作为 `HCx remark` 注入原 Session 并继续执行。
- **备注交互改为卡片内完成**：HC 卡片增加必填多行备注输入框和“提交备注并继续”按钮，表单提交后直接携带 `form_value.remark_note` 续跑，不再要求用户二次 @bot；文本补充入口仍作为兼容兜底保留。
- **群聊 `/new` 误触发 test**：入站文本保留了 `@热点周报助手`，导致机器人名称中的“热点周报”命中触发词。Gateway 现先剥离开头的机器人 mention，再解析 `/new` 和运行模式。
- **后台停止误显示完成**：方舟人工停止会发送 `user.interrupt`，随后仍发送 `session.status_idle(end_turn)`；旧逻辑忽略 interrupt 并把 idle 当完成。Gateway 现将其落为 `stopped` 并显示“已停止”；无 HC 且无最终发布 URL 的提前结束也不再标记完成。
- **无效 HC3 拦截**：`feishu_doc_url` 为空说明 Phase F 未发布成功，Gateway 现在将其标记为失败，不再生成可误点“通过”的 HC3 卡片；Coordinator 同步禁止用本地 Markdown 路径替代飞书文档。
- **飞书权限说明**：创建报告目录使用应用身份权限 `space:folder:create`（“创建云空间文件夹”）；旧文档中的 `drive:drive` 表述已移除，明确不得误选 `drive:drive:version`。
- **Phase E 依赖**：Environment 增加 `openai>=1.0`，避免沙箱运行时临时安装。
- **E2 日历**：默认改为直接读取 `topic6-fetch-normalize/references/marketing_calendar_2026.csv`，并保留 Markdown 日历兼容。
- **报告周期**：`pipeline_f.py` 避免在 `period_label` 已含日期时重复拼接日期范围。
- **验证**：Topic6 完整测试集 `397 passed`。

### C2 向量模型切换为 Doubao-embedding-vision

- **触发现象**：旧默认模型 `Doubao-embedding` 调用标准 `/api/v3/embeddings` 时返回 `InvalidEndpointOrModel.NotFound`。
- **依据**：方舟模型详情页当前首推准确模型 ID `doubao-embedding-vision-251215`；文本输入使用 `POST /api/v3/embeddings/multimodal`。
- **实现**：
  - `04_build_embeddings.py` 默认模型切换为 `doubao-embedding-vision-251215`，支持 `EMBEDDING_MODEL_ID` 和 `--model` 覆盖。
  - vision 接口每条文本单独请求，使用 `--concurrency` 并发；兼容响应 `data` 为对象或列表的两种结构。
  - 显式指定标准 embedding 模型时继续使用 `/embeddings` 批量协议，保留兼容性。
  - Coordinator 和 Event Registry 操作文档同步准确模型 ID、接口及故障排查口径。
- **验证**：Topic6 完整测试集 `392 passed`。

### MA 资源全量更新单入口

- 新增 `update_ma.sh`，统一完成配置加载与校验、全量测试、5 个 Skill 打包和强制上传、Environment/Memory 更新、3 个 Agent 重建、资源 ID 校验与 Gateway 配置回写。
- 脚本不管理 Gateway 进程；执行完成后仍需手动重启 Gateway，使新 Agent ID 和本地代码生效。

### demo 抽样量降至 50 条

- **触发现象**：demo 使用 500 条样本时，C0/C3 仍受 DataHub 吞吐限制，端到端演示等待时间过长。
- **实现**：demo 调用 `sample_500.py --size 50`，test 继续使用 `--size 500`；兼容既有合并与断点恢复逻辑，产物文件名仍为 `sample_500.xlsx`，实际行数以 `status.sample.sample_rows` 为准。
- **口径**：50 条仅用于流程演示，不用于标注质量或正式业务结论；报告和 HC1 卡片同步标明 demo 样本量。

### DataHub 默认模型切换为 Doubao-Seed-Evolving

- **依据**：方舟 Environment 实际调用 `/api/v1/model/list` 返回 113 个模型，确认精确 ID `Doubao-Seed-Evolving` 可用。
- **实现**：Coordinator 与 Annotator 的默认 `--model-id` 从 `Doubao-pro-32k` 切换为 `Doubao-Seed-Evolving`；仍以接口返回列表做运行前精确校验，并以 completion metadata 的实际模型和成本记账。

### DataHub 模型 ID 预检与单次纠错重试

- **触发现象**：C3 使用 `doubao-pro-32k` 创建任务时，DataHub 因模型 ID 大小写敏感返回 `invalid model_id`；子 Agent 受“出错立即结束”约束，没有用查询到的 `Doubao-pro-32k` 重试。
- **根因**：Coordinator/Annotator 契约固化了错误大小写，且模型列表查询发生在输入文件上传和任务创建之后。
- **实现**：
  - 模型 ID 统一修正为 `Doubao-pro-32k`。
  - `datahub_annotate.py` 在上传前调用 `/api/v1/model/list`，打印模型数量和完整 ID 列表，并做大小写敏感的精确校验；无效 ID 会提示唯一的大小写候选，不再产生无用 Data Source。
  - Annotator 遇到 `invalid model_id` 时允许按模型列表候选修正参数并最多重试一次；其他错误仍立即回报。
- **本地验证限制**：本机直调模型列表返回 `ip not allowed`，需在已加入 DataHub IP 白名单的方舟 Environment 中观察真实列表。

### Phase C 性能与稳定性优化，新增 500 条演示模式

- **触发现象**：一次 test 轨迹中，C0/C3 虽于 `10:35:24` 并发启动，但 500 条数据分别耗时约 41/42 分钟；C0 使用约 572 万 tokens，C3 使用约 258 万 tokens。两路 DataHub 任务成功后均未返回 `result_url`，导致统一脚本报错，子 Agent 被迫手工从 `result_list` 分页恢复结果。
- **根因**：
  - DataHub 单任务吞吐约 12 行/分钟，是本轮墙钟时间的主要瓶颈；C0/C3 Prompt 较长则进一步放大 token 成本。
  - DataHub 成功响应存在两种结果形态：下载链接 `result_url`，或内联/分页 `result_list`；原脚本只支持前者。
  - 子 Agent 契约包含不存在的 `--output`、字符串 `--run-id` 和错误的成本命令，造成启动前纠错与手工兜底。
  - C0 解析失败行此前仍会进入 R1~R5，一条失败最多放大为五路无效调用。
- **实现**：
  - `datahub_annotate.py` 在 `result_url` 缺失时自动解析并分页拉取 `result_list`，兼容嵌套输入字段和结果字段别名，严格校验最终行数及 `llm_result`。
  - C0 筛选改为只有明确判定“是否营销可用=是”的记录进入 R1~R5；解析失败/缺失占比超过 5% 仍熔断。
  - 修正 Annotator/Coordinator 契约：Prompt 由脚本直接读取，不再先灌入 Agent 上下文；`run_id` 使用整数；成本按 completion metadata 的实际模型、平台、token 和 `total_consume` 记录。
  - Environment 增加 `lunardate>=0.2.2`，避免 C3 后处理临时安装依赖。
- **demo 模式**：
  - 新增触发词 `热点周报 demo`，固定使用 500 条分层样本。
  - 流程为 `A/B → 抽样 → C/D → HC1 → E/F → HC3 → G/H`；HC1 通过后明确跳过全量 C/D 和 HC2。
  - demo 使用独立的 `wide_table_demo_r{N}.xlsx`，不伪装成 full；最终报告自动标注“基于 500 条分层样本，仅供流程演示，不可作为正式全量结论”。
  - 原 `test` 保持“样本校准后继续全量”的语义，`full` 保持直接跑全量。
- **未默认启用**：Prompt 大幅裁剪、模型切换和 DataHub 多分片并发仍需先做质量、限流与重复计费基准，避免以未经验证的方式影响生产结果。
- **验证与发布**：完整测试集 `382 passed`；5 个 Skill 打包校验通过；已更新 Environment 并重建 Annotator、Insighter、Coordinator。
- **影响文件**：
  - [topic6_runner.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_runner.py)
  - [agents/coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
  - [agents/annotator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/annotator.system.md)
  - [agents/insighter.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.system.md)
  - [datahub_annotate.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/scripts/datahub_annotate.py)
  - [c0_filter_usable.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/scripts/c0_filter_usable.py)
  - [pipeline_e.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/scripts/pipeline_e.py)
  - [pipeline_f.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/scripts/pipeline_f.py)
  - [environment.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/environment.json)

### 对齐 2026-09-29 Managed Agents 文档更新

- **MCP Toolset**：`mcp_toolset` 必须作为 `tools[]` 条目，并通过 `mcp_server_name` 与 `mcp_servers[]` 一一对应；权限策略形状为 `default_config.permission_policy.type`。已修正 `agents/coordinator.json`，继续对只读热点数据 MCP 显式使用 `always_allow`。
- **SSE 启动顺序**：打开事件流后必须等到 `: ready`，再发送首个 `user.message`。已由共享 `_EventStream` 在进入上下文前消费 ready 信号，Topic 6 runner 无需自行解析 SSE 注释。
- **Session 终态**：`session.status_terminated` 是不可继续发送事件的终态，已按失败处理；正常轮次完成仍以 `session.status_idle` 为准。
- **Skill 上传限制**：上传 ZIP 不超过 30 MiB；解压后单文件不超过 30 MiB、总大小不超过 120 MiB、最多 500 个文件；统一顶层目录下直接包含唯一 `SKILL.md`。`tools/pack_skills.sh` 已同步全部门禁。
- **Memory 更新**：创建同路径 Memory 不会覆盖原内容。`create_all.sh --update-memory` 现在先按 path 查找 Memory ID，存在则调用更新接口，不存在才创建。
- **无需修改**：Topic 6 仅挂载 5 个 Skills，未触及单 Agent 50 个上限；Memory 继续使用 `read_only`，不启用本次新增明确化的 `read_write` 能力；Multi Agent 仍是一层协调器到子 Agent，符合嵌套限制。

## 2026-09-24

### Agent Prompt 静态路径与 skill 挂载目录逐字符对齐

- **现象**:coordinator/insighter prompt 里 8 处 `/mnt/skills/...` 死链,导致 Coordinator 首次运行读契约文件时报 `not_found`。具体包括 `topic6-annotation/prompt/`(单复数错)、`/mnt/memory/topic6/xxx.md` 占位举例、`topic6-event-registry/scripts/00_run_all.sh` 不存在、`marketing_calendar_2026.csv` 错误 skill 前缀、`topic6-web-report/scripts/build-report.mjs` 不存在、`topic6-insight/ks/07_报告结构.md` 错误 skill 前缀等。
- **根因**:方舟沙箱把 skill 包挂载在 `/mnt/skills/{skill_key}/`,是**包内目录的直投射**——不做路径重写、不做别名、不容错。prompt 里所有静态路径必须逐字符对应 `ma-resources/skills/{skill_key}/` 下的真实布局。
- **影响文件**:
  - [agents/coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
  - [agents/insighter.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.system.md)
  - [skills/topic6-annotation/prompts/](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/prompts)(补齐 11 个 prompt md,含 `run_config契约.md` / `00_角色与触发.md` / `01_pipeline总览.md` 等)
  - [skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv)(补齐)
- **应对规约**:后续任何 Agent Prompt 修改,必须跑一次静态校验(见 `/tmp/check_topic6_paths.py`)作为准入 gate;新 skill 上线时同步核对 SKILL.md 里的目录索引与磁盘实际结构。

## 2026-09-24

### SKILL.md 必须带 YAML frontmatter,且 `name` 匹配 `^[a-z0-9-]{1,64}$`

- **现象**:`POST /api/v3/skills` 返回 `400 InvalidParameter`,body 里明确报 `SKILL.md frontmatter name must match ^[a-z0-9-]{1,64}$ (got "topic6-fetch-normalize · v1")`。
- **根因**:方舟 CreateSkill 会强制解析 `SKILL.md` 的 YAML frontmatter,`name` 是 skill 的稳定标识,只允许小写字母/数字/连字符,长度 1~64。若 frontmatter 缺失,则退化到用 H1 标题当 name——H1 含空格、中文、`·` 就直接 400。
- **影响文件**:
  - [topic6-fetch-normalize/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/SKILL.md)
  - [topic6-annotation/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/SKILL.md)
  - [topic6-insight/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/SKILL.md)
- **参考**:[火山方舟 MA 文档](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1451-L1470)("按以下约束组织自定义 Skills")、event-registry / web-report 两个已经带 frontmatter 的 SKILL.md 做参照。
- **应对规约**:后续新增 skill 必须先写 frontmatter(`name` / `version` / `description`),再落 Markdown 正文。命名统一走 `topic6-<kebab-case>`。

### CreateSkill 必须带 `X-Ark-Beta: agentic-2026-06-01` header

- **现象**:`POST /api/v3/skills` 返回 `404 Not Found`,body 为空(不是 401/403,方舟直接当路径不存在)。同样规律也命中 `/api/v3/environments` `/api/v3/memory_stores` `/api/v3/agents` `/api/v3/sessions`——只要不带 beta header,curl 一律 404。
- **根因**:整个 Managed Agents 面(environments / memory_stores / agents / sessions / skills)都属方舟 **agentic beta 面**,和普通 v3 API(chat/embedding 等)不共用路由。beta 面要求请求头显式声明版本 `X-Ark-Beta: agentic-2026-06-01`,不带就路由不到,直接 404。
- **影响文件**:
  - [tools/upload_skills.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/tools/upload_skills.py)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(所有 curl 统一走 `ark_post` 封装,顺带解决 response 有时包 `.data` 壳的问题——用 `.data.id // .id` 兼容)
- **参考**:同仓 [ark_min.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/ma-replica/skills/ma-replica-builder/scripts/ark_min.py#L29-L30) 已验证过的写法、[MA 文档 L1477](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1477)。
- **应对规约**:所有直接打 `/api/v3/*` (MA 面) 的脚本必须带 beta header。走火山官方 SDK 的话 SDK 会自动补,不用管。

### CreateEnvironment 请求体必须嵌套在 `config` 下,且字段名固定为 `packages.pip` / `env` / `tos`

- **现象**:`POST /api/v3/environments` 返回 `400`。
- **根因**:方舟 Environment 的 API 契约把所有沙箱配置塞在 `config` 对象里,顶层只放 `name` / `description`。若把 `type` / `networking` / `pip_packages` / `env_vars` 直接平铺到顶层,或用 `pip_packages` / `env_vars` 这类自造字段名,后端解析不到必需字段,返回 InvalidParameter。方舟对未知字段的容忍度比想象中低——不认识的字段直接 400,不会 silently ignore。
- **影响文件**:[ma-resources/environment.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/environment.json) 整体重构。
- **正确形状**(节选,详见 [MA 文档 L2430-L2490](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L2430)):
  ```json
  {
    "name": "...",
    "description": "...",
    "config": {
      "type": "cloud",
      "networking": { "type": "unrestricted" },
      "packages": { "pip": [...], "apt": [...] },
      "env": { "KEY": "VALUE" },
      "tos": { "bucket": "...", "prefix": "..." }
    }
  }
  ```
- **额外副作用**:方舟对 `config.env` 里的 `${VAR}` 字面串**不做二次插值**,不预处理就会把 `"${DATAHUB_ENDPOINT}"` 死字符串灌进沙箱。故 [create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh) 在 POST 前用 `envsubst` 展开一次。
- **应对规约**:后续任何 environment.json 改动,`config` 之外只加 `name`/`description`;未知字段(如自造的 `_output_storage_disabled`)禁止入库,注释走 markdown/changelog。

### Memory `path` 必须以 `/` 开头

- **现象**:`POST /api/v3/memory_stores/{id}/memories` 返回 `400 InvalidParameter: path must start with /`。
- **根因**:方舟 Memory Store 的 path 用绝对路径语义(会被沙箱只读挂载到 `/mnt/memory/{path}`),入参必须以 `/` 开头。写成相对路径(如 `topic6/MEMORY.md`)后端不做归一化,直接 400。
- **影响文件**:
  - [ma-resources/memory-store.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/memory-store.json)(3 条 path 全部补 `/` 前缀)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(POST 前兜底补 `/`,防未来漏改)
- **应对规约**:后续任何 memory-store.json 新增条目,path 必须写 `/topic6/...`。Agent prompt 里引用时,挂载点是 `/mnt/memory` + path,即 `/mnt/memory/topic6/MEMORY.md`,与旧口径完全一致。

### 内置工具必须通过 `agent_toolset_20260701` 配置

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: tools[0].type: unsupported tool type "bash"`。
- **根因**:方舟 MA 把 `bash` / `read` / `write` / `edit` / `glob` / `grep` / `web_fetch` / `web_search` **聚合成一个内置工具集**,type 只写一个 `agent_toolset_20260701` 就默认全开(见 [MA 文档 L1881-L1898](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1881))。除内置工具集外，`tools[]` 还可包含 `custom`、`evolution` 和 `mcp_toolset`；不能把 `bash`、`read` 等单个内置工具名直接写成 type。
- **影响文件**:
  - [agents/annotator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/annotator.json)
  - [agents/insighter.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.json)
  - [agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json)
  - 全部改为 `[{"type": "agent_toolset_20260701"}]`
- **应对规约**:后续新增 Agent 定义，内置工具统一使用 `agent_toolset_20260701`；通过 `configs[].enabled` 控制单个工具启停，通过 `default_config.permission_policy` 或 `configs[].permission_policy` 控制执行前是否确认。

### Agent `mcp_servers[].type` 必填,当前仅支持 `"url"`

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: mcp_servers[0].type: must be "url" (got "")`。
- **根因**:方舟 MCP server 定义走 discriminated union,`type` 是分派字段——即便当前实现只有 URL 一种,也必须显式声明(未来可能引入 `stdio`/`sse`/`streamable_http` 等子类)。省略等价于类型未定,后端一律拒绝。
- **影响文件**:[agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json) `mcp_servers[0]` 补 `"type": "url"`。
- **应对规约**:后续任何 Agent 定义,`mcp_servers[]` 每条必须至少含 `type` / `name` / `url` 三字段,不留隐式默认。
