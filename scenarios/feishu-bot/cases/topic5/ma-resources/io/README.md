# io · 对外契约与接口

> `io/` 存放 T5 的**机器可读契约**:流水线 DAG、节点连接、依赖、A2A 消息口径。人读的流程说明在 `knowledge/03_流程与节点.md`,交接契约在 `identity/coordinator/handoff.md` 与 `knowledge/architecture/03_交接契约与四回执.md`。

## contracts/

| 文件 | 内容 | 状态 |
|---|---|---|
| `pipeline.v0.2.yaml` | **权威** 29 节点 schema + 治理层,engine 直接驱动 | active |
| `connections.v0.2.json` | 节点连接/路由边 | active |
| `DEPENDENCIES.v0.2.md` | A/B/C/D 四类依赖说明 | active |
| `pipeline.v0.2.html` | v0.2 可视化 | 参考 |
| `pipeline.v0.1.yaml` | 旧版 | 历史对照 |
| `pipeline_dag.yaml` / `pipeline_spec.html` | 早期 DAG/规格 | 历史对照 |
| `topic5_业务确认清单.html` | 业务确认清单 | 参考 |

## 契约口径

- 换真只换 handler 函数体,**不改** 节点 schema(id/handler/inputs/outputs/next/routes)、幂等键模板、Artifact 版本化规则、治理层契约、decision 路由键。
- A2A 消息类型与公共信封见 `knowledge/architecture/03_交接契约与四回执.md`:`analysis.request` / `task.accepted` / `clarification.*` / `confirmation.*` / `task.progress` / `artifact.ready` / `validation.request` / `validation.result` / `revision.request` / `delivery.ready` / `deliverable.accepted` 等。
- 四种回执不可混:①消息ACK ②任务受理 ③执行完成 ④交付接收。

## 运行期产物去向

运行期事实(attempts/artifacts/validation/delivery)**不写这里**,统一进 `ma-workspaces/.../runs/{run_id}/`。io/ 只放契约定义。
