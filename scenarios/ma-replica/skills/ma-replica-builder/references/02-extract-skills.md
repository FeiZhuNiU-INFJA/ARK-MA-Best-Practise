# 02 · 重建 skill（渐进式披露，未露出的留空标注）

## 背景：渐进式披露

很多自研 Agent 的 system prompt 里**只放 skill 的简短目录**（名字 + 一句话描述），
详细 SOP 正文要靠一个"加载器"工具（本例叫 `skill_invoke`）在运行时按需拉取。
所以一条轨迹只会披露"这次实际用到的" skill 及其子文档，其余的**根本没出现在轨迹里**。

MA 有自己的 skill 机制（Claude Skills 格式 + harness 按需读取），**不需要** `skill_invoke` 这个工具。
我们的做法是：把轨迹里 `skill_invoke` 返回的正文，转成 MA 能直接挂载的 `SKILL.md` 树。

## 怎么做

`build_skill_bundle.py` 是**冻结契约转换器**（层②）：它只读中间产物 `skill_bodies.json`，
不碰原始轨迹，所以直接用冻结层的那份、换客户不用改。

```bash
cd scenarios/ma-replica/skills/ma-replica-builder
python scripts/build_skill_bundle.py --out-dir <工作目录>
# 读 <工作目录>/skill_bodies.json，产出 <工作目录>/skills/<skill_code>/SKILL.md + references/*.md + index.json
```

- 每个顶层 skill → `skills/<skill_code>/SKILL.md`（YAML frontmatter 的 `name` + 从正文标题/首段
  提炼的 `description`，正文剥掉加载器头部）。
- skill 正文里引用到的子文档（`xxx.md`）：
  - **轨迹里已披露** → 写真实内容到 `references/xxx.md`；
  - **轨迹里未披露** → 也生成 `references/xxx.md`，但**内容留空并显式标注**：
    `> ⚠️ 该子文档在提供的轨迹中未被披露，内容留空；补更多覆盖到它的轨迹即可补全。`
- `index.json` 记录每个 skill 的 `docs`（已写）和 `missing`（留空标注）清单。

## 想重建得更完整？→ 喂更多轨迹

未披露子文档的数量直接反映"轨迹覆盖不足"。**补更多、覆盖不同分支的轨迹**再重跑 extract，
`build_skill_bundle` 会自动把新披露的子文档填上、从 `missing` 移出。这也是"轨迹越多重建越完整"的含义。

## 换客户注意

- 加载器工具名不一定叫 `skill_invoke`；这是**层③ extract 的事**（现写时认这个客户的加载器名与
  子文档参数名）。本样例用 `--skill-loader / --doc-field` 参数化，你现写时照着改成客户的名字即可。
- 若客户 Agent 根本没有"加载器"（skill 正文直接写在 system 里），那 `skill_bodies.json` 会是空的，
  这一步跳过，skill 内容留在 system prompt 里即可。
- 无论加载器怎么变，只要 extract 吐出的 `skill_bodies.json` 符合契约
  （`{key: {skill_code, document_key, result, is_error}}`），`build_skill_bundle.py` 就照跑不用改。
