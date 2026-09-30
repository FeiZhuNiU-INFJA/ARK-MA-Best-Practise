# 妙搭 HTML 发布流程

本流程发布的是**用户已有的 HTML 交付物**。发布源来自本地文件，不是把需求文本交给妙搭 AI 重新生成。

## 1. 输入与确认节点

必要输入：

- `source_path`：单个 `.html`，或包含 `index.html` 的目录。
- `target`：`new`、现有 `app_id`，或已有妙搭仓库。
- `progress`：是否播报；开启时必须给出 `chat_id` 或 `user_id`，并确认发送身份与模板，详见 [progress.md](progress.md)。

新建时还需要应用名；描述可选。若用户只给源路径且明确要求发布，默认创建新的 `html` 应用。若发现多个同名应用或多个可能的 `app_id`，停止并让用户确认，不能靠模糊匹配覆盖应用。

以下操作必须单独确认：

- 把更新发布到一个已有生产应用，而用户未明确要求覆盖它。
- 调用 `+access-scope-set` 改变谁能访问。
- 任何清理、删除或回滚动作。

## 2. 本地预检

```bash
# 以“当前 Skill 目录”为基准运行，不硬编码任何平台专属安装布局（不同 Agent 平台安装目录各异）。
python3 "${SKILL_DIR:?}/scripts/validate_html.py" <source_path>
```

`SKILL_DIR` 指本 Skill 被安装到的实际目录（即 SKILL.md 所在目录）；调用前可先 `SKILL_DIR="$(dirname 本引用文件所在 references 的上级)"` 或直接 `cd` 进 Skill 目录后运行 `python3 scripts/validate_html.py <source_path>`。退出码 `0` 表示没有阻断错误；退出码 `2` 表示不可发布。警告不是自动失败，但必须在继续前评估。

阻断项包括入口缺失、无效 UTF-8、缺失本地资源、目录逃逸引用、敏感凭证文件。`data:` 资源和外部网络资源会被统计；它们不证明页面运行可靠。

## 3. 身份与版本

最低能力基线为 `lark-cli 1.0.95`。不同 shell/Agent 的 PATH 可能命中不同安装版本，先确定绝对路径：

```bash
python3 scripts/resolve_lark_cli.py
MIAODA_CLI_BIN="<resolver 返回的 selected.path>"
"$MIAODA_CLI_BIN" --version
"$MIAODA_CLI_BIN" auth status --verify --json
```

后文示例中的 `lark-cli` 均表示该次任务已解析并固定的绝对路径，不能在同一任务中重新依赖 PATH。若 resolver 找不到 `>=1.0.95` 的版本，停止并报告候选路径与版本。

不要因为开始任务就强制重新登录。只有命令明确提示未登录或 apps scope 不足时才执行：

```bash
lark-cli auth login --domain apps --json
```

若当前运行环境无法等待设备授权，使用 `--no-wait --json`，把验证链接交给用户；用户确认后再用返回的 `device_code` 完成授权。不得记录 device code、token 或 cookie。

## 4. 新建妙搭 HTML 应用（默认）

### 4.1 创建并初始化

```bash
lark-cli apps +create \
  --as user --format json \
  --name "<app_name>" \
  --app-type html \
  --description "<description>"

lark-cli apps +init \
  --as user --format json \
  --app-id "<app_id>" \
  --dir "<repo_dir>"
```

`<repo_dir>` 是本次发布的本地妙搭仓库工作目录，**应落在发起用户的项目目录内**，不要放到 Skill 目录、系统区或随意的 scratch 位置。生产环境按落位规范（方案A）置于用户分区，例如 `<用户项目目录>/03_数据接收及处理/miaoda_repo/` 或该项目下的专用子目录（`<用户项目目录>` = `Users/{工号}_{姓名}/projects/{Topic}/{项目名}/`）；一次性测试可用用户分区的 `tmp/`。这样仓库与项目其他产物同源、可追溯，不污染系统/角色目录。

`+init --source-path` 虽然存在，但不能把“命令成功”当作源文件已经导入。初始化后必须检查目标仓库内的 `index.html` 和资源；缺失时，把预检通过的源文件复制到仓库工作树，再执行 `git status --short` 确认变更。

不要把 `.env`、`.npmrc`、云凭证目录或密钥文件带入仓库。不要删除初始化产生的妙搭元数据。

### 4.2 提交和推送

在初始化的妙搭仓库内：

```bash
git status --short
git add -- <明确的文件或目录>
git commit -m "Publish predefined HTML"
git push origin HEAD:sprint/default
```

只添加本次交付文件，不能用会把无关或敏感文件一起纳入的宽泛操作。若 Git 鉴权失败：

```bash
lark-cli apps +git-credential-init --as user --format json --app-id "<app_id>"
```

然后重试原 push；不能因此改走遗留 `+html-publish`。

### 4.3 创建并确认发布

`+release-create` 发布的是已经推送到远端的代码，不是尚未提交的工作区：

```bash
lark-cli apps +release-create \
  --as user --format json \
  --app-id "<app_id>" \
  --branch sprint/default

lark-cli apps +release-get \
  --as user --format json \
  --app-id "<app_id>" \
  --release-id "<release_id>"
```

以短间隔轮询，直到 `finished` 或 `failed`；单次持续等待不超过 60 秒。若运行环境超时，保留 `app_id` 和 `release_id`，下一次从 `+release-get` 恢复，不要重复创建应用或发布。

如果创建请求结果不确定，先恢复性查询：

```bash
lark-cli apps +list --as user --format json --keyword "<exact_app_name>" --ownership mine
lark-cli apps +release-list --as user --format json --app-id "<app_id>" --page-size 20
```

## 5. 更新已有妙搭应用

先确认本地目录确实属于目标应用，检查妙搭元数据、Git remote 和用户提供的 `app_id` 是否一致。更新流程为：预检源文件 → 复制到该仓库 → 精确 `git add` → commit → push `sprint/default` → `+release-create` → `+release-get`。

不得仅凭应用名模糊命中后覆盖。若本地仓库缺失，可通过 `+init --app-id <app_id>` 重新初始化。

## 6. 遗留非 Git HTML 应用

只有 `+get` 和既有交付记录明确证明该应用是遗留直传模式时，才使用：

```bash
lark-cli apps +html-publish \
  --as user --format json \
  --app-id "<app_id>" \
  --path ./relative/source
```

不得对新建 `html`/创意应用优先使用它。不得用 `--allow-sensitive` 绕过凭证扫描，除非用户理解具体文件且明确授权；通常应移除敏感文件。

返回 `release_id` 时继续用 `+release-get`；直接返回 URL 时仍应查询应用详情确认发布状态。

## 7. 获取真实链接

按以下顺序读取，首个非空值即为候选：

1. 发布响应中的 URL 字段。
2. `lark-cli apps +get --app-id <app_id>` 的 `online_url`。
3. `lark-cli apps +list --keyword <exact_name>` 后，按 `app_id` 精确过滤出的 `online_url`。

妙搭 HTML 发布可能在 `release-get=finished` 时仍不返回 URL，`+get` 也可能只返回 `meta_token`；这不代表没有链接。继续按 `app_id` 查询 `+list`，不得按域名和 meta token 自行拼 URL。

## 8. 输出契约

成功时返回：

```json
{
  "provider": "feishu-miaoda",
  "operation": "create|update|legacy-publish",
  "app_id": "...",
  "release_id": "...",
  "release_status": "finished",
  "online_url": "https://...",
  "source_path": "...",
  "source_sha256": "...",
  "reused_url": false,
  "verification": {
    "api_release_finished": true,
    "visual_verified": false
  }
}
```

不声称已经做过未执行的视觉验证。失败或未决时返回已有 ID、最后一次 API 状态、错误原文摘要和下一条恢复命令。

`release-get`、`release-list` 的原始响应可能包含发布人的姓名和邮箱。只在内存中解析，并仅交付 `release_id`、`status`、`online_url`、`commit_id`、时间戳和错误摘要；不得保存或回显发布人个人字段。
