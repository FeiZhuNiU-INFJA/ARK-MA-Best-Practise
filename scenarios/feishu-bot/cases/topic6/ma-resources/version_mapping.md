# Skill 版本对照表

> 修改 Prompt/脚本 → 跑 `pack_skills.sh` + `upload_skills.py`,SkillHub 侧生成新 version 号,
> 协调器 Prompt 里 pin 到明确 version,不使用 latest。
>
> 每次上传后,upload_skills.py 会往 skill_ids.json 追加 {skill_id, version, source_sha256}。
> 本文件作为**人类可读的对照表**,由维护人手工同步。

---

## 当前映射

所有 skill 从 `ma-resources/skills/topic6-*/` 打包。

| Skill 目录 | skill_id | version | 内含活跃 Prompt/脚本版本 | 上次更新 |
|---|---|---|---|---|
| ma-resources/skills/topic6-fetch-normalize/ | (未上传) | - | Phase A+B 归一化脚本 v1(热度基准 2026 内置 JSON) | 2026-09-24 |
| ma-resources/skills/topic6-annotation/ | (未上传) | - | C0=v5, R1=v2, R2=v1, R3=v1, R4=v2, R5=v2, 节点标注=v7;datahub_annotate.py 为 5 合 1 精简版 | 2026-09-29 |
| ma-resources/skills/topic6-insight/ | (未上传) | - | 共享 role_style 拼接;E1=v1, E2=v3, E3=v1, E4=v6 + _tagging=v1 | 2026-09-24 |
| ma-resources/skills/topic6-event-registry/ | (未上传) | - | v2.2.0；跨平台单入口、断点续跑与运行签名 | 2026-09-29 |
| ma-resources/skills/topic6-web-report/ | (未上传) | - | v1.1.0 | 2026-09-24 |

## 打包冒烟结果(2026-09-24 首轮)

| ZIP | 大小 | 说明 |
|---|---|---|
| topic6-fetch-normalize.zip | 11 KB | 3 脚本 + 基准 JSON |
| topic6-annotation.zip | 99 KB | 5 合 1 datahub_annotate + cost-tracker + 7 prompts + 3 references |
| topic6-insight.zip | 66 KB | pipeline_e/f + 6 统计脚本 + 4+1 prompts + 2 references |
| topic6-event-registry.zip | 177 KB | 21 脚本 + references 全套 |
| topic6-web-report.zip | 4.1 MB | 含模板资产、图片、字体(仍远低于 30 MiB 上传上限) |

## 更新流程

1. 变更 `ma-resources/skills/topic6-*/` 下的 prompt/脚本
2. 运行 `tools/pack_skills.sh` → 生成新 zip 到 `tools/out/`
3. 运行 `tools/upload_skills.py` → 更新 `skill_ids.json`
4. 更新本表格 + `ma-resources/agents/coordinator.json` 的 `skills[].version` 字段
5. 直接重跑 `create_all.sh` 推送协调器配置到 MA(Agent 会自动重建)
