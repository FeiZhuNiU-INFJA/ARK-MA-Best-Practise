# Changelog — miaoda-web-publish

## 1.0.1 — 2026-09-22

### Changed
- `references/publish.md` §2 预检命令：把硬编码的 `$CODEX_HOME/skills/miaoda-web-publish/scripts/validate_html.py` 改为**平台无关的通用写法**——以「当前 Skill 目录」（`SKILL_DIR` / SKILL.md 所在目录）为基准运行 `scripts/validate_html.py`，不再绑定 `$CODEX_HOME`、`.claude/skills` 等任一平台专属安装布局。修复原路径在 `.claude/skills/` 安装布局下的误导性。

### Added
- `references/publish.md` §4.1：为 `+init --dir "<repo_dir>"` 增加落位约束说明——本地妙搭仓库工作目录应落在**发起用户的项目目录内**（方案A：`Users/{工号}_{姓名}/projects/{Topic}/{项目名}/`），一次性测试可用用户分区 `tmp/`；禁止放到 Skill 目录、系统区或随意 scratch 位置，保证与项目其他产物同源、可追溯、不污染系统/角色目录。

### Notes
- 纯文档修订，不改脚本逻辑与命令行为。
- 背景与实测详见 `Agent.Supervisor/Logs/change_log/entries/20260922-miaoda-publish-and-whitelist-fix.md`。
