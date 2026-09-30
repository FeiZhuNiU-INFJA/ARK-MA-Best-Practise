---
name: miaoda-web-publish
version: 1.0.1
description: 基于飞书妙搭（Miaoda/Spark）和 lark-cli apps，把用户已有的 HTML 文件或静态网页交付物校验、创建或更新妙搭应用、发布并返回 API 提供的可访问链接；也用于查询妙搭应用详情、发布历史、访问分析、运行指标、日志和链路。用户提到“妙搭发布 HTML”“复刻现有网页”“更新妙搭网页”“查询妙搭 PV/用户数/日志/性能”时使用。只处理妙搭，不把同名的通用网页部署、AI 生成页面或浏览器后台抓取混入此流程。
---

# 妙搭网页发布与数据查询

> **与相似 skill 分工（Ada 接入补充）**：本 skill 负责把 HTML **发布到飞书妙搭（Miaoda）并返回访问链接 + 查询妙搭 PV/日志/性能**，控制面是 `lark-cli apps`。与 `artifact-template-bluefocus-hotspot-web-report` 互补：后者负责用 Topic6 模板**生成/构建**自包含 HTML（含 CDN 快照上传），本 skill 负责把成品 HTML **托管发布到妙搭**；两者是"生成→发布"的上下游，不重叠。与 `Export_to_Feishu`（飞书文档发布）目标形态也不同（妙搭 Web 应用 vs 飞书文档）。

本 Skill **明确基于飞书妙搭（Miaoda/Spark）**。唯一控制面是 `lark-cli apps` 所调用的妙搭 API；不得用浏览器内置页、管理后台 DOM 或页面抓取获取发布状态和业务数据。

## 先判断任务

按用户目标选择一路，不要默认执行全部操作：

- 发布已有 HTML：读取 [references/publish.md](references/publish.md)。
- 查询访问人数、PV、运行指标、日志或链路：读取 [references/analytics.md](references/analytics.md)。
- 在执行中向飞书播报关键进度：读取 [references/progress.md](references/progress.md)。
- 评估这次发布可能涉及的成本：读取 [references/cost.md](references/cost.md)。
- 登录、权限、数据口径或能力边界有疑问：读取 [references/auth-and-limits.md](references/auth-and-limits.md)。

## 能做什么

- 校验一个本地 `.html` 文件或以 `index.html` 为入口的静态目录，并输出 SHA-256、资源引用、缺失文件与敏感文件风险。
- 使用当前飞书用户身份创建妙搭 `html` 应用，初始化妙搭 Git 仓库，提交、推送、创建发布并轮询结果。
- 用 `app_id` 或本地妙搭仓库更新同一个应用；成功更新通常沿用原链接。
- 仅对已确认的遗留非 Git HTML 应用调用 `apps +html-publish`。
- 通过妙搭 API 查询应用详情、发布状态与发布历史，并返回 API 实际给出的 `online_url`。
- 查询妙搭在线应用的聚合用户数、活跃/新增用户、页面浏览量，以及请求量、错误、延迟、CPU、内存、日志和 trace。
- 在用户确认接收人、消息模板和发送身份后，通过飞书 IM API 播报发布关键节点，并用幂等键避免重复消息。
- 说明现有 HTML 直发与妙搭 AI 生成的成本边界，并基于用户提供的价格表做可审计估算。
- 在 API 支持时查询访问范围；仅在用户明确要求时修改访问范围。

## 不能做什么

- 不把 HTML 上传到任意第三方托管；目标只能是飞书妙搭。
- 不默认让妙搭 AI 重新生成、改写或设计页面；输入 HTML 是发布源，而不是提示词。
- 不承诺像素级复刻或运行时视觉一致；API 发布成功只证明发布链路成功。视觉验收需要用户另行授权可用的验证方式。
- 不通过妙搭内置分析返回访客昵称、具体姓名或逐人浏览历史；该接口是聚合分析。日志/trace 的 `user-id` 过滤也不等于身份目录查询。
- 不从 CLI/API 查询单次生成消耗的 AI 点数、套餐余量或人民币账单；当前网页发布与分析命令不提供这些计费字段。
- 不把 `null`、空序列或尚未结算的数据解释成 0。
- 不在访问范围 API 不支持某类应用时，改用浏览器后台绕过限制或推测公开状态。
- 不绕过飞书登录、权限、发布审批或组织策略。
- 不自动修改数据库、角色、成员、环境变量、自动化或开放密钥；这些属于其他妙搭运维任务。

## 固定执行契约

1. **确认输入**：识别源路径、目标（新建或现有 `app_id`）、期望应用名、是否只查询；同时确认进度播报的 `chat_id`/`user_id`、发送身份和消息模板。目标歧义会改变发布对象时必须让用户确认。用户已明确说“发布这个 HTML”即视为发布授权，但不等于授权向未确认的飞书会话发消息。
2. **预检**：运行 `scripts/validate_html.py <path>`。有错误就停止；警告应呈现给用户并根据风险决定是否继续。
3. **CLI 与身份检查**：先运行 `scripts/resolve_lark_cli.py`，保存它返回的绝对路径，后续始终使用同一可执行文件；再检查身份。只有 CLI 明确返回未登录或缺少 scopes 时，才执行 `auth login --domain apps`。不得主动清除或覆盖现有身份。
4. **发布路由**：新应用使用 Git 管理流程；现有仓库更新同一应用；只有确认是遗留非 Git HTML 应用才用 `+html-publish`。
5. **发布确认**：创建发布后用 `+release-get` 轮询到 `finished` 或 `failed`。超时不等于失败，应返回 `release_id` 并给出恢复命令。
6. **链接解析**：优先取发布响应中的 URL；若 `finished` 没有 URL，调用 `+list` 或 `+get`，按精确 `app_id` 读取 `online_url`。不得手工拼接链接。
7. **进度播报**：若已配置并确认，在预检通过、应用就绪、代码推送、发布开始、发布结束等节点调用飞书 IM API；发送失败记录为 `progress_delivery_error`，默认不把已成功的发布回滚。
8. **结果交付**：至少返回 `app_id`、`release_id`、状态、`online_url`、源文件 SHA-256、应用是否新建、是否复用原链接和各播报节点的 `message_id`；失败时返回已知 ID 和可恢复步骤。

所有妙搭命令都带 `--as user --format json`。路径参数尽量使用当前工作目录下的相对路径；命令输出只保存必要字段，不持久化 token、cookie、凭证、发布人姓名或邮箱。
