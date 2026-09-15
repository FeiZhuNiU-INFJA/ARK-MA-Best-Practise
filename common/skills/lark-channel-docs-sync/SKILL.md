---
name: lark-channel-docs-sync
description: 同步 lark-channel-sdk 官方仓库（larksuite/channel-sdk-python）docs/ 目录下的全部 Markdown 为单个合集文件。逐文件抓取原文、把文档间相对链接改写为合集内锚点、用 Prettier 统一格式，幂等生成/更新 common/docs/lark_channel_sdk_docs.md。当用户要求更新该合集、或从该仓库 docs/ 抓取拼接 SDK 文档时使用。
---

# lark-channel-docs-sync：lark-channel-sdk 文档合集同步

把飞书官方 **lark-channel-sdk** 的 Python 仓库
（`github.com/larksuite/channel-sdk-python`）`docs/` 目录下的全部 Markdown，
抓取拼接成一个合集文件。

本仓库默认目标：`common/docs/lark_channel_sdk_docs.md`。

## 为什么是快照合集，而不是 git submodule

对齐同目录 `volc-docs-sync` 的既有范式：**外部文档 → 抓成快照进 `common/docs/`
纯文件**。相比 submodule 的好处：

- 只取 `docs/` 一个子目录，不必把整个 SDK 源码仓拖进来（submodule 只能整仓挂载）。
- 无需 `git clone --recursive` / `submodule update --init`，克隆即可用，自包含。
- 文档是给人/给 Agent 看的参考资料，不是代码依赖；SDK 本体已由
  `pip install lark-channel-sdk` 管理。

## 何时使用

- 用户说「更新 `common/docs/lark_channel_sdk_docs.md`」/「同步 channel sdk 文档」。
- 需要从 `larksuite/channel-sdk-python` 的 `docs/` 导出一份合集。

不负责：需要登录鉴权的私有文档；飞书开放平台的权限 scope 文档（那在
open.feishu.cn / open.larksuite.com，不在 SDK 仓库里）。

## 工作原理（一句话）

先调 GitHub `git/trees?recursive=1` 列出 `docs/` 下所有 `.md`，再用
`raw.githubusercontent.com` 逐个取原文，做「相对链接→合集锚点」改写 + 一级标题降级，
拼上头部/目录/来源，最后 Prettier 格式化并做代码围栏完整性自检后写文件。

关键事实（踩坑记录）：
- 用 `raw.githubusercontent.com/{repo}/{ref}/{path}` 取**原文**，不要抓 HTML 页面。
- 文档之间用相对链接互引（`./quickstart.md` 等）；合并成单文件后会失效，脚本按
  当前文件目录规范化路径后改写成 `#doc-xxx` 锚点，无法解析的原样保留。
- 每页正文的一级标题 `# x` 降为 `##`，避免与合集里每页的 `## 文件名` 抢层级。
- GitHub 匿名 API 有速率限制；命中限流时 `list_doc_paths` 会报错提示，稍后重试即可。

## 用法

```bash
# 默认：更新本仓库的合集（main 分支 docs/ -> common/docs/lark_channel_sdk_docs.md）
python3 common/skills/lark-channel-docs-sync/update_docs.py

# 只抓取并打印诊断（每个文件路径 / 字节数），不写文件
python3 common/skills/lark-channel-docs-sync/update_docs.py --dry-run

# 钉某个版本 tag（与本地已装 SDK 版本对齐，避免拿到不匹配的新接口）
python3 common/skills/lark-channel-docs-sync/update_docs.py --ref v1.4.0

# 跳过 Prettier（环境无 npx 时；输出为拼接原文，可能有格式抖动）
python3 common/skills/lark-channel-docs-sync/update_docs.py --no-format
```

参数：`--repo`（默认 `larksuite/channel-sdk-python`）、`--ref`（分支/tag，默认
`main`）、`--out`（输出路径，默认自适配到 `common/docs/`）、`--no-format`、
`--dry-run`、`--sleep`（请求间隔秒）。

## 依赖

- Python 3（仅标准库，无需 pip 安装）。
- Prettier：通过 `npx --yes prettier@3` 自动获取（需要 Node/npx 与网络）。
  缺失时脚本自动降级为不格式化并打印告警，不会硬失败。

## 建议校验（更新后）

```bash
f=common/docs/lark_channel_sdk_docs.md
grep -cE '<a id="doc-' "$f"          # 锚点数 = docs/ 下 md 文件数
grep -c   '来源：`docs/' "$f"        # 来源行数 = 文件数
test $(($(grep -o '```' "$f" | wc -l) % 2)) -eq 0 && echo "代码围栏成对 OK"
```
