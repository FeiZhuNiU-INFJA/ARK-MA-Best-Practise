# 场景：方舟 MA × headless 浏览器 —— 扫码登录脉脉并抓取 FDE 职位

用火山方舟 **Managed Agents（MA）** 在云端沙箱里跑一个 headless 浏览器：打开脉脉（maimai.cn）登录页 →
切到扫码登录、把**二维码**截图回传到 MA 客户端由你扫 → 登录后搜索「FDE」职位 → 保存搜到的**前 10 个**。
若登录过程中出现**扫码之外的额外验证**（滑块 / 短信验证码 / 人脸等），脚本会**暂停并通知你**，等你处理。

> 这是一个「长时任务 + 人工介入（扫码/验证）」的 MA 最佳实践骨架。按既定决策：**流程骨架优先** ——
> 目标站点用脉脉真实页面；任何一步抓不到就**降级为可控样例数据**，保证整条链路始终可复现、可演示。

> **关于「能不能不登录就查职位」**：实测脉脉的职位搜索页在未登录时会被**重定向回登录页**，拿不到职位数据。
> 所以本 demo 走**扫码登录**：脚本把二维码回传给你，由你本人用脉脉 App 扫码（扫码只能你本人完成，不可代跑）。

## 为什么这样设计（核心约束）

MA 沙箱是**无 GUI 的云端 Linux**，且 **Agent 的每次 bash 调用之间进程不保活**。而扫码登录的二维码会话
绑定在**同一个浏览器进程**里（从展示二维码→手机扫→服务端确认登录，必须由同一个常驻 Chromium 持有）。
所以不能「开浏览器→退出→再开浏览器」分段做，必须：

- **沙箱内**：用一个**后台常驻单进程**（`nohup ... &`）从头跟到尾，所有进度写成**文件信号**到
  `/mnt/session/outputs/`（该目录写入会自动注册到 Files API）。
- **编排侧**：与 Agent 对话轮次解耦地、按固定节奏**直接轮询 Files API** 下载 `state.json` / 二维码 /
  结果，据此驱动「提示你扫码」「暂停通知」「收尾下载」。

状态机（`state.json` 的 `phase`）：
`starting → waiting_qr_scan → (need_manual_verify) → logged_in → searching → done`（任何环节失败转 `error` 并降级到 `done`）。

## 职责边界（谁在什么阶段做什么）

| 阶段 | 责任主体 | 具体动作 | 关键产物 / 信号 |
| --- | --- | --- | --- |
| 装机 | **MA Environment** | `setup_script` 首次启动沙箱时装 `ca-certificates` + `pip install playwright` + `playwright install --with-deps chromium` | `/workspace/bootstrap/ready` |
| 上传 & 挂载 | **编排脚本** `run_demo.py` | 上传 `sandbox/maimai_login_search.py` 到 Files API，建 Agent/Env/Session 并把脚本挂到 `/mnt/session/uploads/` | `file_id`、`session_id` |
| 拉起 | **MA Agent** | 收到指令后用一条 `nohup python3 ... &` **后台拉起**常驻脚本，立刻返回 | `sandbox.log` |
| 扫码登录 | **沙箱脚本**（常驻进程） | 打开登录页、切到扫码视图、截二维码 canvas、轮询登录态、检测额外验证 | `state.json`、`qrcode.png`、`verify.png` |
| 提示扫码 | **编排脚本** | 轮询到 `waiting_qr_scan` → 下载 `qrcode.png` 到本地并（尝试）打开，提示你扫码 | 本地 `out/qrcode.png` |
| 扫码 | **你（人工）** | 在 MA 客户端 / 本地打开二维码，用脉脉 App 扫码确认登录 | —— |
| 额外验证 | **沙箱脚本 + 编排脚本** | 命中滑块/短信/人脸 → 脚本置 `need_manual_verify` 并暂停；编排侧**暂停并通知你**，等你处理 | `verify.png` |
| 处理验证 | **你（人工）** | 按通知处理验证；脚本检测到已登录后自动继续 | —— |
| 搜索抓取 | **沙箱脚本** | 登录后 dump 职位页结构（`job_page.html/png`），搜「FDE」并抓前 10 个职位；抓不到→降级样例 | `result.json`、`result.md`、`job_page.*` |
| 收尾 | **编排脚本** | 轮询到 `done` → 下载 `result.*`、`sandbox.log` 到本地 `out/`，打印汇总 | 本地 `out/` |

## 目录结构

```text
maimai-fde-browser/
├── README.md                     # 本文件
├── env.example                   # 环境变量样例（复制为 config.env 填 ARK_API_KEY）
├── ark_client.py                 # 自包含方舟 MA 客户端（仅依赖 httpx，不引用主项目包）
├── agent_config.py               # Agent 定义 + 沙箱装机脚本（Playwright/Chromium）
├── run_demo.py                   # 编排入口：建资源→拉起→轮询→人工介入→收尾
├── sandbox/
│   └── maimai_login_search.py    # 沙箱内后台常驻脚本：扫码登录 + 搜 FDE + 状态机 + 降级
└── out/                          # 运行产物（二维码/结果/日志；.gitignore 忽略）
```

## 上手

本场景**自包含**，只依赖 `httpx`（不碰场景1 `arkagent` 包，也不用它的 conda 环境）。

```bash
cd scenarios/maimai-fde-browser
python3 -m pip install httpx            # 唯一依赖
cp env.example config.env               # 填入 ARK_API_KEY（或直接 export ARK_API_KEY=...）
python3 run_demo.py
```

运行后：

1. 脚本依次建 Agent / Environment（装 Playwright+Chromium，首次较慢）/ Session，并让 Agent 后台拉起登录脚本。
2. 二维码就绪时，终端会提示并把 `out/qrcode.png` 自动打开 —— **用手机脉脉 App 扫码登录**。
3. 若出现扫码之外的额外验证，终端会**暂停并通知你**，`out/verify.png` 是现场截图；处理完成后自动继续。
4. 登录成功后自动搜「FDE」、抓前 10 个职位，产物下载到 `out/`：`result.json`、`result.md`。
   - 脉脉登录后的职位页真实结构尚未完全探明，脚本会把 `job_page.html` / `job_page.png` dump 到产物目录，
     便于据此精调选择器；若通用选择器没命中，则降级为样例并在 `result.json` 标注 `degraded=true`。

常用参数：

```bash
python3 run_demo.py --keep             # 结束后保留 agent/env/session 便于复查
python3 run_demo.py --timeout 900      # 调大轮询整体超时（秒）
python3 run_demo.py --interval 3       # 调整轮询间隔（秒）
```

### 可调环境变量（沙箱脚本侧，均可选）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAIMAI_SEARCH_KEYWORD` | `FDE` | 搜索关键词 |
| `MAIMAI_TOP_N` | `10` | 保存前 N 个职位 |
| `MAIMAI_LOGIN_TIMEOUT_S` | `300` | 扫码+登录等待上限（秒） |
| `MAIMAI_HEADLESS` | `true` | 是否无头（沙箱必须 true） |
| `MAIMAI_JOB_URL` | （空） | 直接指定职位搜索页 URL（探明真实结构后填，`{kw}` 处会被关键词替换） |

> 要改沙箱脚本的默认值，可在 `agent_config.py` 的 `ENV_PACKAGES` 之外给 Environment 传 `env=...`，
> 或直接改脚本常量。本 demo 为简洁默认不透传，保持配置集中。

## 合规与边界说明

- 本 demo 用于**技术链路演示**：如何在 MA 里编排「长时浏览器任务 + 扫码/验证的人工介入」。
- **只登录你自己的账号、只读公开检索结果**；是否自动化访问某站点、如何使用抓取到的数据，请遵守该站点的
  用户协议与 robots 规则，以及适用的法律法规。
- 脉脉有风控，页面结构与反爬策略随时可能变化。真实抓取失败时脚本会**明确标注降级样例**
  （`degraded=true` + 原因），绝不冒充真实数据——这既是演示的诚实底线，也保证链路可复现。
