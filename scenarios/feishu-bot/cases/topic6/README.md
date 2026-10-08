# Topic 6 · 社媒热点周刊

Topic 6 是一个运行在火山方舟 Managed Agents（MA）上的飞书 Bot 场景。用户在飞书中发送触发词后，系统会自动完成四平台热点取数、标准化、AI 标注、洞察生成、人工审核和报告发布。

## 文档导航

- [快速调试与完整配置](./TOPIC6_DEBUG_QUICKSTART.md)
- [变更日志](./TOPIC6_CHANGELOG.md)
- [客户文件迁移映射](./CUSTOMER_TO_MA_FILE_MAPPING.md)
- [架构说明](./topic6_ma_architecture.html)
- [完整流水线](./topic6_pipeline_overview.html)

## 运行模式

| 飞书指令                   |            数据范围 | 审核流程                              | 适用场景                     |
| -------------------------- | ------------------: | ------------------------------------- | ---------------------------- |
| `热点周报 demo`          |       50 条分层样本 | HC1 → 报告 → HC3                    | 推荐用于快速演示和端到端验收 |
| `热点周报 full`          | 先 500 条，再跑全量 | HC1 → 全量 C/D → HC2 → 报告 → HC3 | 客户完整模式，正式生产       |
| `热点周报 skip_sampling` |          直接跑全量 | HC2 → 报告 → HC3                    | 跳过 500 条采样校准          |

`demo` 模式不会触发全量标注和 HC2。生成的报告会明确标注“基于 50 条分层样本，仅供流程演示，不可作为正式全量结论”。

## 开始使用

首次部署、环境变量、飞书权限、资源上传、Gateway 启动和故障排查统一维护在[快速调试与完整配置](./TOPIC6_DEBUG_QUICKSTART.md)，本文件不重复展开。

完成部署后，建议先发送：

```text
热点周报 demo
```

验收时确认 C0/C3 并发执行、HC1 后没有进入全量标注与 HC2，并且最终报告带有样本演示声明。

资源或代码更新后，应遵循（在仓库 `scenarios/feishu-bot/cases/topic6/` 目录下执行）：

```bash
./update_ma.sh
```

该脚本会完成测试、Skill 打包上传、Environment/Memory 更新、Agent 重建和资源 ID 回写；Gateway 仍需手动重启。详见[快速调试与完整配置](./TOPIC6_DEBUG_QUICKSTART.md#34-全量更新所有-topic-6-资源)。

每位需要触发报告并发布到妙搭的用户，首次使用前在 Gateway 主机运行（在仓库 `scenarios/feishu-bot/` 目录下执行）：

```bash
python cases/topic6/authorize_miaoda_user.py
```

脚本会打开飞书授权页，并在扫码成功后自动读取扫码者的 `open_id`；无需手工传
`open_id`。需要防止扫错账号时，可额外传 `--expected-open-id ou_xxx` 做校验。

## 系统组成

Topic 6 使用 `1 Coordinator + 2 类子 Agent`：

| 组件        | 职责                               |
| ----------- | ---------------------------------- |
| Coordinator | 编排阶段、并发委派、人工审核和发布 |
| Annotator   | 执行 C0、C3、R1-R5 DataHub 标注    |
| Insighter   | 并发生成 E1-E4 四个洞察版块        |

主要 Skill：

| Skill                      | 阶段                             |
| -------------------------- | -------------------------------- |
| `topic6-fetch-normalize` | 热点取数、清洗、标准化、抽样     |
| `topic6-annotation`      | C0/C3/R1-R5 标注、筛选与宽表合并 |
| `topic6-event-registry`  | C2 事件识别与跨平台合并          |
| `topic6-insight`         | E1-E4 洞察生成与报告合并         |
| `topic6-web-report`      | 网页版报告生成                   |
| `miaoda-web-publish`     | 妙搭 HTML 应用发布与发布状态确认 |

运行中间产物位于 `/workspace/Projects/<project_dir>/`，最终交付物写入 `/mnt/session/outputs/`。

## 维护约定

`ma-resources/` 是部署资源的唯一源码目录；不要直接修改 `tools/out/` 中的 ZIP。行为变化同步记录到[变更日志](./TOPIC6_CHANGELOG.md)，部署操作统一维护在[快速调试与完整配置](./TOPIC6_DEBUG_QUICKSTART.md)。
