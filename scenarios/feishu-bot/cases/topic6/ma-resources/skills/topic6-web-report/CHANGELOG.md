# 变更记录

## 1.1.0-ma-fork.1 — 2026-09-24 (MA fork)

从 `artifact-template-bluefocus-hotspot-web-report` v1.1.0 fork,重命名为 `topic6-web-report`。
业务规则、模板资产、校验逻辑完全对齐原版,仅做以下 MA 适配改动:

- skill 名替换:`artifact-template-bluefocus-hotspot-web-report` → `topic6-web-report`
  (SKILL.md 头 frontmatter、`agents/openai.yaml` default_prompt)。
- SKILL.md 顶部加 MA fork 注释;`<skill-directory>/scripts/*.mjs` 的字面挂载路径在 MA 环境下解析为
  `/mnt/skills/topic6-web-report/scripts/`(调用方按 skill 挂载路径拼接,无需改脚本代码)。
- 无脚本代码逻辑改动、无参数/环境变量增减、无模板资产替换。
- 后续跟随 MA 版本上的实际问题演进,不再同步 upstream。
