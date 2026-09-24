# MA Skill 版本对照表

> 客户改 Prompt/脚本触发 pack_skills.sh + upload_skills.py,MA SkillHub 侧生成新 version 号,
> 协调器 Prompt 里 pin 到明确 version,不使用 latest。
>
> 每次上传后,upload_skills.py 会往 skill_ids.json 追加 {skill_id, version, source_sha256}。
> 本文件作为**人类可读的对照表**,由维护人手工同步。

---

## 当前映射(MA 迁移后 v1 基线)

自 2026-09-24 起,所有 skill 从 `ma-resources/skills/topic6-*/` 打包,与 `assets_from_customer/`
完全解耦;不再跟 upstream 同步。业务口径与客户原版一致(以下"内含活跃版本"记录 fork 时的
客户版本快照,供追溯用)。

| MA skill 目录 | MA skill_id | MA version | 内含活跃 Prompt/脚本版本(fork 快照) | 上次更新 |
|---|---|---|---|---|
| ma-resources/skills/topic6-fetch-normalize/ | (未上传) | - | Phase A+B 归一化脚本 v1(热度基准 2026 内置 JSON) | 2026-09-24 |
| ma-resources/skills/topic6-annotation/ | (未上传) | - | C0=v4, R1=v2, R2=v1, R3=v1, R4=v2, R5=v2;datahub_annotate.py 为 5 合 1 精简版 | 2026-09-24 |
| ma-resources/skills/topic6-insight/ | (未上传) | - | 共享 role_style 拼接;E1=v1, E2=v1(取客户 v3), E3=v1, E4=v1(取客户 v6)+_tagging/v1 | 2026-09-24 |
| ma-resources/skills/topic6-event-registry/ | (未上传) | - | fork 自 blueai-canonical-event-registry v2.1.0(MA fork.1) | 2026-09-24 |
| ma-resources/skills/topic6-web-report/ | (未上传) | - | fork 自 artifact-template-bluefocus-hotspot-web-report v1.1.0(MA fork.1) | 2026-09-24 |

## 打包冒烟结果(2026-09-24 首轮)

| ZIP | 大小 | 说明 |
|---|---|---|
| topic6-fetch-normalize.zip | 11 KB | 3 脚本 + 基准 JSON |
| topic6-annotation.zip | 99 KB | 5 合 1 datahub_annotate + cost-tracker + 7 prompts + 3 references |
| topic6-insight.zip | 66 KB | pipeline_e/f + 6 统计脚本 + 4+1 prompts + 2 references |
| topic6-event-registry.zip | 177 KB | 21 脚本 + references 全套 |
| topic6-web-report.zip | 4.1 MB | 含模板资产、图片、字体(仍远低于 50MB 上限) |

## 更新流程

1. 变更 `ma-resources/skills/topic6-*/` 下的 prompt/脚本
2. 运行 `tools/pack_skills.sh` → 生成新 zip 到 `tools/out/`
3. 运行 `tools/upload_skills.py` → 更新 `skill_ids.json`
4. 更新本表格 + `ma-resources/agents/coordinator.json` 的 `skills[].version` 字段
5. 通过 `create_all.sh --update-agent` 推送协调器配置到 MA

