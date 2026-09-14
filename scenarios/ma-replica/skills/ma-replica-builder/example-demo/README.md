# Worked example：3 条合成客服工单分诊轨迹（example-demo）

一份**完全虚构、可外发**的完整样例：3 条合成的「Acme 客服平台·工单分诊」轨迹，把
`ma-replica-builder` 的全流程跑通。它既是**回归基准**（脚本改动后重跑，产物应与 `data/` 一致），
也是**换客户前的参照样板**（示范层③客户生成式脚本长什么样、产出什么契约）。

> ⚠️ 本样例不含任何真实客户信息——轨迹、工单、客户、接口全是造的，故意覆盖下游各分支
> （渐进式披露留空、键冲突、错误返回、下划线 skill_code…），好让离线管线跑出一份有代表性的回归基准。

## 场景

3 条轨迹都是同一类任务：资深客服组长盘点一个工单（如"T-1001 被重复扣费怎么处置""T-1002
客户问退款政策""T-1003 客户要升级投诉"），Agent 通过统一网关 `api_call` 拉取工单/客户/交互日志
业务数据，套用 `ticket_triage` 等技能的 SOP，产出一份处置卡片（严重度 / SLA / 处置建议 / 话术）。

## 轨迹来源

`example-demo/trajectories/trajectory{1..3}.json`（随 skill 入库；每个是一次自研 Agent 的完整运行，顶层 `messages[]`）。

## 复现（离线部分，无需 ARK_API_KEY）

```bash
# 任意有 httpx 的 py3.11 环境即可（例：conda activate <your-env>）
cd scenarios/ma-replica/skills/ma-replica-builder

# 准备 case 工作目录：把 3 条合成轨迹放进场景级目录 ../../ma-cases/demo/trajectories/
mkdir -p ../../ma-cases/demo/trajectories
cp example-demo/trajectories/trajectory*.json ../../ma-cases/demo/trajectories/

# 层③ 客户生成式（这个客户现写）：把原始轨迹拆成中间产物契约（读 trajectories/、写 shared/）
python example-demo/scripts/extract_trajectories.py --case-dir ../../ma-cases/demo
# 层② 冻结契约转换器（只吃中间产物，换客户不用改）
python scripts/build_skill_bundle.py  --case-dir ../../ma-cases/demo
python scripts/gen_file_mocks.py      --case-dir ../../ma-cases/demo

# 与 skill 自带的回归基准比对（应无差异；example-demo/data 是入库的参照样本）
diff -rq ../../ma-cases/demo/shared example-demo/data --exclude=ma_runs --exclude=comparison.md
```

> `../../ma-cases/` 是 per-case 运行期数据（已 gitignore），不入库；固化的参照产物在 `example-demo/data/`，
> 可直接查看，无需重跑。旧口径 `--out-dir <dir>` 平铺目录仍兼容。

## 抽取结果（已验证）

- **api 回放键**：9（工具名 `api_call`），另加 1 个 `current_time` → `replay_map` 共 10 条。
- **skill 正文/子文档**：5 份（加载器 `skill_invoke`），重建成 3 个 SKILL.md：
  - `ticket_triage`：主文档 + 2 个已披露子文档（`severity-rubric.md` / `sla-policy.md`）；
    **2 个未披露**（`escalation-paths.md` / `response-templates.md`）已留空标注。
  - `kb_search`：主文档；**2 个子文档全未披露**（`query-syntax.md` / `ranking-signals.md`）留空标注。
  - `macro_suggester`：仅主文档。
- **未披露子文档共 4 个** —— 直接印证"轨迹越多、重建越完整"：补覆盖到这些分支的轨迹再重跑，
  它们会被自动填上。
- **键冲突 1 处**（同键不同返回），抽取端按"取最长成功版"归并。
- **错误回放键 1 个**（`api|query_object|obj=interaction_log|where=ticket_id=T-1003`）——录制里就是错误返回，
  严格回放会如实回放这个错误，不编造成功数据。

覆盖矩阵详见 [data/summary.md](data/summary.md)。3 条 query 见 [data/queries.json](data/queries.json)。

## 两套 mock 产物

- **变体 A**（custom tool 动态回放）：用 `data/replay_map.json` + `data/skills/`，由
  `scripts/run.py --mock custom` 在事件循环里严格回放。
- **变体 B**（文件静态 mock）：`data/mocks-skill/` —— 10 条调用物化成
  `mocks/*.txt`（含 1 条错误返回），台账 `mocks/INDEX.md`，可粘进 system 的
  `data/mock_system_appendix.md`。

## Live 实跑（需 ARK_API_KEY）

离线三步已验证通过；`⑤ 在 MA 实跑 + ⑥ 对比`需要方舟凭据。层③ 客户脚本 `run.py`
（工具面 + 结果路由 + 自研耗时）会调层① 冻结引擎跑完并**自动出对比报告**：

```bash
export ARK_API_KEY=...
cd scenarios/ma-replica/skills/ma-replica-builder
python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --model doubao-seed-evolving --all   # 默认 files 模式
# 或高保真：python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock custom --all
# 产出 ../../ma-cases/demo/files-mode/<traj>/run.json（或 custom-mode/）+ ../../ma-cases/demo/reports/comparison-<mode>.md
```

跑过之后把 `bash 抢戏` 观测结论与耗时/token/cache 数字回填到 [../PROBLEMS.md](../PROBLEMS.md)。
