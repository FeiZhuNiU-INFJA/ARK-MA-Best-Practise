---
name: volc-docs-sync
description: 同步火山方舟（Volcengine Ark）文档中心的一段连续页面为单个 Markdown 合集文件。通过文档中心 getDocDetail 接口抓取每页自带的「复制 Markdown」原文，做链接归一化并用 Prettier 统一格式，幂等生成/更新合集。当用户要求更新 common/docs/火山方舟_ManagedAgents_docs.md，或按 DocumentID 区间从 docs.volcengine.com 抓取拼接文档合集时使用。
---

# volc-docs-sync：火山方舟文档合集同步

把火山方舟文档中心（`docs.volcengine.com/docs/{library}/{doc_id}`）一段
**连续 DocumentID 区间**的页面，抓取拼接成一个 Markdown 合集文件。

本仓库默认目标：`common/docs/火山方舟_ManagedAgents_docs.md`
（Managed Agents 文档，DocumentID `2553713..2553731`，共 19 页）。

## 何时使用

- 用户说「更新 `common/docs/火山方舟_ManagedAgents_docs.md`」/「同步方舟 MA 文档」。
- 用户给出 `docs.volcengine.com/.../{start}..{end}` 形式的区间，要求抓取拼接。
- 需要按 DocumentID 区间从火山方舟文档中心导出「复制 Markdown」原文合集。

不负责：需要登录鉴权的私有文档；非火山方舟文档中心的站点。

## 工作原理（一句话）

页面正文是客户端渲染的 SPA，但文档中心提供内部接口
`GET https://docs.volcengine.com/api/doc/getDocDetail?DocumentID={id}&lang=zh`，
其返回的 `Result.MDContent` 就是页面「复制 Markdown」按钮的原文。脚本逐页抓取
该字段 → 归一化链接 → 拼 header/目录/来源 → Prettier 格式化 → 写文件。

关键事实（踩坑记录）：
- 用 `MDContent` 字段，**不要**用 `Content`（那是 Quill delta JSON，不是 Markdown）。
- 正文里的 `docs.volcengine.com/docs` 统一改写为 `www.volcengine.com/docs`
  （对齐既有合集的对外规范域名）；只有「来源」行保留 `docs.volcengine.com`。
- 2026-10 起火山方舟新前端启用语义化路由 `ark.volcengine.com/.../docs/ark/{slug}`，
  这类链接**保持原样**不改写；旧站数字 ID 路由 `/docs/82379/{id}` 仍可同时存在。
  数据接口仍按 DocumentID 查询，与前端 slug 迁移完全解耦，无需改脚本。
- 用 Prettier（`--parser markdown --prose-wrap preserve`）统一表格对齐、锚点空行、
  CJK 强调间距，保证幂等、只反映真实内容变化，不产生格式抖动。
- 多语言代码示例用 `<Tabs>/<Tab>/<TabTitle>` 自定义 HTML 标签包裹（如「SDK 完整示例」
  的 Python/Go/Java 三个 tab）。这类内容也在 `MDContent` 里，会被完整保留。脚本在
  格式化后做一次**完整性自检**：核对 ` ``` ` / `<Tabs>` / `<TabTitle>` 数量未减少，
  若被吞会打印 `[warn]`，避免静默产出残缺文件。
- 幂等验证：线上未变更的页面重复运行后 diff 近似为空。

## 用法

```bash
# 默认：更新本仓库的方舟 MA 合集（2553713..2553731 -> common/docs/火山方舟_ManagedAgents_docs.md）
python3 common/skills/volc-docs-sync/update_docs.py

# 只抓取并打印诊断（每页标题 / MDContent 长度 / 最近更新时间），不写文件
python3 common/skills/volc-docs-sync/update_docs.py --dry-run

# 自定义区间与输出路径（复用到其它文档合集）
python3 common/skills/volc-docs-sync/update_docs.py \
    --start 2553713 --end 2553731 \
    --out common/docs/火山方舟_ManagedAgents_docs.md

# 跳过 Prettier（环境无 npx 时；输出为拼接原文，可能有格式抖动）
python3 common/skills/volc-docs-sync/update_docs.py --no-format
```

参数：`--start/--end`（闭区间 DocumentID）、`--out`（输出路径，默认基于脚本位置
自适配到 `common/docs/`）、`--no-format`、`--dry-run`、`--sleep`（请求间隔秒）、
`--probe-radius`（边界探测半径，默认 5；0 跳过）。

## DocumentID 边界与自适应

- DocumentID 是火山引擎**全局递增、跨产品共享**的 ID，**不保证连续**。相邻 ID 很
  可能来自其它库（如 `cr` / `Cloudphone` / `DoubaoVoice`），**不能**简单扩区间。
- 判定"属于本合集"的可靠特征：`LibraryCode == "ark"` **且** `ParentCode` 在抓取
  时收集到的父节点集合内（本次区间 `2553713..2553731` 对应 `managed-agents-*` 的
  若干组）。
- 脚本在抓取完成后自动做 `±probe-radius` 的边界探测：对两侧 ID 发轻量请求，命中
  上述特征的会**只打印提示**（不自动改区间，避免误并入他库文档）。例如 2026-10-09
  通过探测发现并纳入 `2553731 定义 Outcome`。再次发现同类提示时，手工评估后调整
  `--start/--end`（或默认值 `DEFAULT_START/DEFAULT_END`）即可。

## 依赖

- Python 3（仅标准库，无需 pip 安装）。
- Prettier：通过 `npx --yes prettier@3` 自动获取（需要 Node/npx 与网络）。
  缺失时脚本自动降级为不格式化并打印告警，不会硬失败。

## 建议校验（更新后）

```bash
# 结构完整性：以下三项都应为区间页数（默认 19）
grep -cE '<a id="doc-[0-9]+"></a>' common/docs/火山方舟_ManagedAgents_docs.md
grep -cE '^\- \[.*\]\(#doc-[0-9]+\)'  common/docs/火山方舟_ManagedAgents_docs.md
grep -c   '来源：\[https'             common/docs/火山方舟_ManagedAgents_docs.md
# 正文不应残留 docs.volcengine.com/docs（应为 0）
grep '来源：' -v common/docs/火山方舟_ManagedAgents_docs.md | grep -c 'docs.volcengine.com/docs'
```

## 下游联动检查

更新 `common/docs/火山方舟_ManagedAgents_docs.md` 之后，**必须同步评估**是否需要
更新 `common/docs/managed-agents-architecture-atlas.html`（基于合集提炼的架构图/能力
清单，与 Markdown 合集同源）。脚本运行结束会打印一条提示作为兜底提醒。

联动触发条件（任一满足即需人工审阅 atlas 是否过期）：

- 本次抓取有页面 `UpdatedTime` 晚于 atlas 文件的 mtime。
- 新增/删除了 DocumentID（`--start/--end` 区间变化）。
- 以下关键页面 `md_len` 变化显著（> 10%）：
  - `2553716 Agent` / `2553719 Tools` / `2553720 工具权限策略`
  - `2553723 启动 Session` / `2553724 管理 Session` / `2553725 Session 事件流`
  - `2553730 编排 Multi Agent`

快速对比命令：

```bash
# atlas 文件最后修改时间
stat -f '%Sm' common/docs/managed-agents-architecture-atlas.html
# 对比脚本输出里每页的 updated 时间，判断是否有页面比 atlas 新
```
