# 变更记录

## 1.1.0 — 2026-09-24

- 脚本挂载路径:`<skill-directory>/scripts/*.mjs` 在部署环境下解析为
  `/mnt/skills/topic6-web-report/scripts/`(调用方按 skill 挂载路径拼接,无需改脚本代码)。
- 无脚本代码逻辑改动、无参数/环境变量增减、无模板资产替换。
