# Seedance Managed Agent × 飞书 Bot

在飞书中描述图片或视频需求，Gateway 将消息交给 Seedance MA；MA 编排检索、图片生成、视频生成和合成工具。群内首次 `@Bot` 开启创作话题，Gateway 持续更新同一张创作卡片，从 Files API 下载产物并回传到话题内。

这是独立 `seedance` case，使用 `~/.arkagent/cases/seedance/` 保存配置与运行状态，可以直接通过 `arkagent run --case seedance` 启动。

## 准备与运行

从仓库根执行，复用现有 Bot Python 环境（Python 3.11+，含仓库依赖）。如需新建环境，见 [场景说明](../../README.md)。首次安装运行 `pip install -e ".[dev]"`。

1. 开通 Managed Agents、Seedance Managed Agent，以及控制台 Seedance 创建页面提供的主模型、图片模型、视频模型和检索工具服务。三个模型必须从该页面选择，不能假定任意通用模型均可用。
2. 准备飞书 Bot。可以使用现有应用，或运行：

   ```bash
   arkagent init --case seedance
   ```

   扫码依赖已有 Node helper 的 npm 依赖。初始化只创建飞书应用并安全保存方舟 Key、飞书凭据，视觉资源由下一步创建。使用现有应用时，将 [env.example](env.example) 复制到 `~/.arkagent/cases/seedance/config.env`，填写真实值。不要覆盖已经初始化的配置。

3. 在 case 配置中补齐三个模型：

   ```text
   SEEDANCE_AGENT_MODEL_ID="${SEEDANCE_AGENT_MODEL_ID}"
   SEEDANCE_IMAGE_MODEL_ID="${SEEDANCE_IMAGE_MODEL_ID}"
   SEEDANCE_VIDEO_MODEL_ID="${SEEDANCE_VIDEO_MODEL_ID}"
   ```

   上面的 `${...}` 是待替换占位符。配置由 Python 作为数据解析，不执行 shell，也不展开变量；请直接将右侧替换为控制台提供的实际模型／推理接入点 ID。

4. 创建或更新 MA 资源，检查并运行：

   ```bash
   bash scenarios/feishu-bot/cases/seedance/update_ma.sh --dry-run
   bash scenarios/feishu-bot/cases/seedance/update_ma.sh
   bash scenarios/feishu-bot/cases/seedance/update_ma.sh --check
   arkagent run --case seedance
   ```

   `update_ma.sh` 使用当前环境的 `python`；可设置 `SEEDANCE_PYTHON` 指定解释器，或用 `--config /path/config.env` 指定配置。Gateway 的配置路径由 `ARKAGENT_HOME` 决定；使用自定义配置文件时应保证二者指向同一文件。`--dry-run` 只渲染配置，`--check` 只读检查方舟资源。

   创建成功后脚本自动合并写回 `SEEDANCE_AGENT_ID` 与 `SEEDANCE_ENVIRONMENT_ID`，文件权限为 `0600`，保留其他配置键。再次执行时优先使用已配置 ID；没有 ID 时按名称分页查找，重名要求明确指定 ID。已有 Agent 和 Environment 原地更新，ID 保持不变。创建操作失败不会自动重试。指定 ID 不可访问时直接报错，修正配置后再执行。

   已有 Seedance Agent 和 Environment 时，可以直接填写这两个 ID，运行 `--check` 后启动 Bot；主模型配置仅在创建／更新时必需。普通 Agent 的 `base_agent` 创建后不可修改，不能直接转换为 Seedance Agent。

同一飞书 App 只允许一个 Gateway 长连接进程；复用其他 case 的 Bot 时，先停止该 App 的旧 Gateway。不同 App 可以并行运行。资源更新后重启 Gateway，并在已有创作中使用 `/new`，让新 Session 使用新配置。

使用现有飞书应用时，启用机器人能力和长连接事件 `im.message.receive_v1`，开通应用身份权限 `im:message`、`im:message:send_as_bot`、`im:resource`，以及卡片更新权限 `im:message:update`。群内引用素材、话题内不 `@Bot` 的续聊需要 `im:message.group_msg`（接收群中所有消息）。配置后发布应用版本，并将 Bot 加入目标群、设置可用范围。Seedance case 无需卡片按钮回调和用户 OAuth。

已有 Seedance 资源时，切换话题卡片只需重启 Gateway，无需重新运行 `update_ma.sh`：

```bash
# 先在旧进程终端 Ctrl-C 停止，再从仓库根运行
conda activate topicX
python -m arkagent run --case seedance
```

## 飞书交互

| 操作 | 行为 |
| --- | --- |
| 单聊发送文字 | 开始或继续创作；同一用户复用 Session，进度和结果更新同一张卡片 |
| 单聊先发送图片／文件，再发送需求 | 附件先保存为参考素材，收到文字需求后上传并用于创作 |
| 群主聊天 `@Bot` 描述需求 | 开启独立创作话题和卡片；每条主聊天新需求独立创建 Session |
| 在已建立的创作话题中发送文字／素材 | 无需再次 `@Bot`；同一发送者继续当前创作，其他发送者使用各自的 Session 和卡片 |
| 群聊引用图片／文件消息并 `@Bot` | 只读取明确引用且属于当前群的素材，不扫描整个群历史 |
| 带附件的富文本消息 | 同时传入需求和参考素材 |
| `/new` | 清空当前对话的素材和映射，下条需求新建 Session；不删除远端 Session，不中断已运行任务 |
| `/status` | 查询当前 MA Session 状态并更新卡片；生成期间也可使用，不覆盖正在显示的工具进度 |
| `/files` | 重新查询并回传当前轮明确声明的最终交付物；不回传检索素材、中间帧和草稿，不重新生成 |
| `/help` | 查看用法 |

例子：

```text
为一家城市精品咖啡品牌制作新品宣传素材。
先生成一张 16:9 电影感主视觉：清晨城市街角，手冲咖啡放在窗边木桌上，
暖色自然光，画面不要出现文字。
再以这张图为首帧生成一段 4 秒、16:9 视频，镜头缓慢推进，咖啡蒸汽自然上升。
```

之后在同一话题内直接继续：“保留构图，把光线改成傍晚，再生成一版图片。” 群中的查询命令也应在对应话题内发送。未登记的话题和普通群消息不会在无 `@Bot` 时触发创作。

卡片集中显示当前状态、Session ID、创作需求、阶段、已用时、更新时间、结果摘要和交付文件。Session 创建后展示完整 ID；替换 Session 时同步更新，`/new` 后清空，便于在方舟侧定位会话。阶段覆盖检索素材、脚本分镜、图片／视频生成、字幕制作、合成、质检和失败恢复；工具描述归纳为固定阶段，不展示内部命令或路径。阶段变化合并后最多每 15 秒更新一次；长工具等待时每 15 秒刷新耗时和更新时间，不新增进度消息，不虚构百分比。

准备／生成／回传阶段使用蓝色，完成使用绿色，结果不完整使用橙色。“等待超时”使用橙色，表示 SSE 等待已超时、云端任务可能仍在执行；Gateway 继续查询云端事件并更新原卡片，观察到终态后自动回传最终交付物。“执行失败”使用红色。

默认回传 JPEG／PNG／WebP 图片消息，以及 MP4 文件消息。**视频当前以文件发送，不提供内嵌播放器卡片。** 原生飞书视频／语音消息暂不作为输入，参考视频请以文件方式发送。

## 运行流程与职责

| 步骤 | 责任主体 | 动作 |
| --- | --- | --- |
| 1 | 飞书 Channel SDK | WebSocket 接收并归一化消息，交给 case |
| 2 | Seedance Gateway | 检查用户白名单、首次群 @ 或已登记话题、事件去重；归一话题根消息 ID 与真实话题 ID，按 `(chat_id, topic_root, user_open_id)` 隔离并串行处理创作；建立或复用创作卡片 |
| 3 | Gateway + Files API | 下载用户明确提供的参考素材、上传方舟；在创建 Session 时通过 `resources` 挂载 |
| 4 | Seedance MA | 通过 `ultra_vision_agent_preview`、基础工具集和多模态工具集编排任务；图片／视频写入 `/mnt/session/outputs/` |
| 5 | Gateway | 先建立 SSE，再发送需求和本轮阶段／交付契约；默认等待 30 分钟，超时后每 15 秒只读回查事件，持续更新原卡片 |
| 6 | Gateway + Files API | 仅匹配当前轮最终交付清单中的真实文件名，同名选择最新版本；默认最多轮询 10 次、间隔 2 秒，等待交付文件登记 |
| 7 | Gateway + 飞书 OpenAPI | 有界下载后上传飞书图片／文件并回复到创作话题，卡片汇总文件；记录已回传的文件 ID，避免后续创作重复回传旧文件 |

纯文本续创复用原 Session。**新增参考素材时创建替代 Session**：重新挂载已有输入、上轮最终交付物和新素材，并注入最近 3 轮创作文字；保留旧 Session 的远端产物。检索素材和工作文件不会占用替代 Session 的挂载额度。此策略遵守项目“运行时不动态增删文件挂载副本”的约束。单个 Session 最多挂载 100 个文件，超过时要求用 `/new` 缩小范围。

默认不把飞书凭据、方舟 Key 注入云端环境，也不需要 MCP、Vault、OAuth 或其他 case 的 Skill。`SEEDANCE_AUTHORIZED_OPEN_IDS` 可以限制使用者；空值表示允许应用可见范围内的用户。

## 配置、产物和失败处理

完整配置见 [env.example](env.example)。视频默认等待 30 分钟，输入与输出文件默认上限各为 30 MiB；飞书图片超过 10 MiB 时按文件回传。大小阈值可调整，但实际飞书接口限制仍以平台为准。

- 等待 Session 终态超过配置上限（默认 30 分钟）时，卡片显示橙色“等待超时”，提示云端任务未被取消；保留 Session 映射，后台继续回查阶段和终态，不自动重发需求。超时后仍可用 `/status` 回查；`/files` 只获取已经明确发布的最终交付物，文件可下载本身不表示任务已经完成。真实执行失败即使有部分交付物也显示红色。
- SSE 断流后保留 Session 映射，可用 `/status`、`/files` 回查；若仍 running，新的创作需求会提示等待。Gateway 重启时恢复正在创作／回传／等待超时的卡片，查询旧任务事件，继续更新阶段并回传最终结果；不重新发送创作需求。已完成回合不自动重发文件。
- 兼容旧版将等待超时保存为“执行失败”的卡片：启动时先核对云端状态，仅在仍 running 或历史证实已成功完成时接续；真实云端失败保留红色。旧卡片中的素材汇总按最终交付清单重新整理，已发送到飞书的历史消息保留。
- 话题根消息与真实话题 ID 的映射、卡片消息 ID 和卡片内容均保存在 case 数据库，重启后继续更新原卡片。`/new` 清空当前发送者的 Session、参考素材和文字上下文，保留话题与卡片供后续使用。
- 卡片发送／更新失败时，关键结果降级为话题内文字，工具进度保持静默，继续执行生成和文件回传；权限提示每次连续失败只显示一次。补齐权限并发布后，新一轮创作会重试卡片发送／更新。
- 每轮动态系统消息要求 Agent 使用 `[stage]` 声明阶段，并在质检后通过 `[deliverables]` JSON 路径数组声明最终交付物；Gateway 只按该清单筛选 Files API 的文件。已有回合兼容从最后一条最终回复的本地 Markdown 输出链接恢复清单，进度中的路径不作为交付依据。没有可信清单时提示等待，不按扩展名或文件黑名单猜测。此逻辑在 Gateway 生效，无需重新运行 `update_ma.sh`。
- 已登记的最终交付文件会先回传；仍未登记的交付文件提示稍后用 `/files` 获取。每次 `/files` 获取最新预签名 URL 并重试回传；同名文件只选最新登记版本。新轮清空旧清单，避免混入上轮交付物。
- 文件超限、下载或飞书上传失败时提供临时下载链接，链接过期后重新 `/files`。回传默认逐文件执行；图片与文件上传均移到线程执行，避免阻塞 SSE 事件循环。
- 卡片最多展示 8 个文件，并按完整链接的字节预算裁剪；超过展示上限的下载链接会单独发送到话题内，保证可获取产物。
- `400 InvalidParameter` 通常与模型、工具配置有关；`403 OperationDenied` 通常表示 Seedance、模型或工具服务未开通。装配入口只输出 HTTP 状态、错误码和 request_id，便于查询服务端问题。
- Environment 默认使用官方教程的云环境与 unrestricted 网络。需要长期保存产物时，在 [environment.json](ma-resources/environment.json) 的 `config` 中配置自己的 `tos`；未配置时使用方舟公共 TOS，注意平台保留期。不要手动删除仍需参考的远端 Session；删除 Environment 前先清理其关联 Session。

## 文件入口

| 文件 | 内容 |
| --- | --- |
| [gateway.py](gateway.py) | 消息分发、素材处理、Session、进度、产物回传 |
| [cards.py](cards.py) | Card 2.0 创作卡片；有界摘要与完整文件链接 |
| [protocol.py](protocol.py) / [progress.py](progress.py) | 回合阶段／交付契约、事件恢复、精确筛选、节流队列和心跳 |
| [store.py](store.py) | case 私有 SQLite 状态，含话题别名和卡片；数据库默认在仓库外 |
| [config.py](config.py) | 配置加载与校验 |
| [resources.py](resources.py) / [update_ma.sh](update_ma.sh) | 统一资源创建、原地更新、检查与 ID 回写 |
| [agent.json](ma-resources/agent.json) | 官方 Seedance 工具及模型配置 |
| [system.md](ma-resources/system.md) | 创作助手提示词与交付契约 |
| [environment.json](ma-resources/environment.json) | 云环境与可选长期产物存储 |

验证命令（仓库根，已安装 dev 依赖）：

```bash
python -m pytest -q scenarios/feishu-bot/tests/test_seedance.py
```

测试使用模拟 MA／飞书接口验证接入链路，不调用生成模型。真实生成需完成服务开通、模型选择和飞书应用配置。

参考：[官方 Seedance MA 教程](https://docs.volcengine.com/docs/ark/visual-creation-agent?lang=zh) · [文件上传、挂载与产物获取](https://ark.volcengine.com/region:cn-beijing/docs/upload-and-mount-files) · [飞书 Bot 权限说明](../../../../common/docs/feishu_bot_permissions.md)。
