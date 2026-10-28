"""把机器可读 DAG 发布成一页 HTML 流程规格。

结构字段（节点、mode、io、next、routes、gate、module、control_plane、副作用等）
实时从 pipeline.v0.2.yaml 读取，保证不漂移；治理层（门/模块/数据·失败·发布策略）
同样取自 yaml 的 governance 段；业务背景 / 源文件映射 / 实现状态作为本文件内置
标注（这些是 DAG 结构之外的人工知识）。

输出：self-contained HTML（mermaid 走 CDN），可直接浏览器打开。
"""
from __future__ import annotations

import html
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PACKAGED_DAG_PATH = HERE / "contracts/pipeline.v0.2.yaml"
SOURCE_DAG_PATH = HERE / "../../io/contracts/pipeline.v0.2.yaml"
DAG_PATH = PACKAGED_DAG_PATH if PACKAGED_DAG_PATH.exists() else SOURCE_DAG_PATH
OUT_PATH = HERE / "../../io/contracts/pipeline.v0.2.html"

OVERVIEW = {
    "title": "Topic5 营销活动复盘报告 · 流程规格 v0.2",
    "background": "评估品牌在社媒平台的营销活动传播效果，帮助客户了解活动期间的声量表现、"
                  "口碑反馈与内容传播质量，为后续营销策略提供数据支撑。",
    "scene": "品牌在公关传播 / 话题活动 / KOL 种草等活动结束后进行效果复盘，对比活动期与基准期声量差异，分析受众反应与情感口碑。",
    "client": "品牌主（消费品 / 快消 / 时尚 / 科技），通常为品牌部或公关部。",
    "value": "量化活动声量与互动效果 · 识别高质量内容特征 · 提炼用户真实口碑 · 给出下次活动优化建议。",
    "source": "v0.2 合成来源：pipeline.v0.1.yaml（治理契约）+ pipeline_dag.yaml（可执行 DAG，已本地跑通）。"
              "双层：可执行 nodes 层 + 声明式 governance 层。",
}

# 状态分类：标签 + 文字色 + 背景色
STATUS = {
    "can_wire":      ("可接真·本地脚本就绪", "#1a7f4b", "#e6f6ec"),
    "has_asset":     ("有脚本/清单·待审计迁入", "#9a6b00", "#fff4d6"),
    "missing_dep":   ("源包依赖缺失·只能 stub", "#b02a2a", "#fdeaea"),
    "control_plane": ("控制面未上线·stub 执行留接缝", "#0f766e", "#e6f5f3"),
    "real_logic":    ("编排逻辑已真实", "#1f5fb0", "#e7f0fb"),
    "human":         ("人工关卡", "#6b3fa0", "#f0e9fa"),
}

# 每节点：业务背景 + 源文件映射 + 实现状态（键 = v0.2 节点 id）
DOCS = {
    "n00_intake": ("复盘报告必须基于完整需求（品牌/活动/周期/关键词/平台/内容范围/业务问题/交付期望）。字段不全直接停，避免下游返工。",
                   ["Agent.Supervisor/ 需求梳理提示词", "需求确认单模板"], "has_asset"),
    "n01_hc_requirement": ("需求快照必须由用户确认（发布门 G1），确认后方可进入方案阶段。",
                           ["流程图 G1 / 需求确认单"], "human"),
    "n02_plan": ("Topic5Agent 产出分析方案/执行方案/模块选择；未登记需求按 unregistered_request_policy 先扩能力再过 G2。",
                 ["Agent.Producer 系统提示词 v0.32", "modules 登记表（governance.modules）"], "has_asset"),
    "n03_hc_plan": ("方案必须由用户确认（发布门 G2）并冻结 ConfirmedPlan（artifact_id + version），作为后续所有生产节点的依据。",
                    ["流程图 G2 / required_before_production"], "human"),
    "n04_run_init": ("冻结资源版本 + 上下文快照 + 输入校验。由控制面 Blueflare-Ada 负责；未上线时本地 stub 执行，保留接入缝。",
                     ["Blueflare-Ada 控制面（未上线）", "failure_policy.checkpoint_source"], "control_plane"),
    "n05_brand_pull": ("品牌活动期整体社媒表现（声量/互动/情感/词云/热门内容），分母=品牌全量声量。体量大，由 SSJH 自动拉聚合值。skill 已迁入，含 cloud_word 等 11 维；SSJH 为付费外部取数，接真需走成本确认门。",
                       ["skills/SSJH-Social-Stat/ · scripts/run.py（付费外部·成本门后接真）"], "has_asset"),
    "n06_brand_baseline": ("基准期=活动前 30 天且不重叠，用于「活动拉升效果」对比。缺失按 conflict_policy 降级该指标并建 DataIssue。同用 SSJH skill，付费外部取数。",
                           ["skills/SSJH-Social-Stat/（付费外部·成本门后接真）", "module: baseline_lift"], "has_asset"),
    "n07_clean": ("活动 Excel 清洗：按周期过滤、id 去重、派生平台标签、派生 kol_level（头/腰/尾），为分层效能分析打基础。",
                  ["test_run/step1_clean.py", "KS_Classification_Rules.md"], "can_wire"),
    "n08_ugc_discovery": ("活动参与方式不预设：LLM 读样本自主发现 UGC 参与方式类别，避免固定四类遗漏新玩法。(dynamic vs fixed_four 为待裁决口径)",
                          ["skills/blueai-phase-ugc-discovery-skill/", "test_run/phase_discovery.py"], "has_asset"),
    "n09_annotate_r1": ("全量内容打两个基础标签：情感 + 是否活动相关。<1000 条走小引擎(claude-haiku)，≥ 走 DataHub 批量。",
                        ["test_run/step2_round1_annotate.py", "/mnt/skills/blueai-datahub-skill/"], "has_asset"),
    "n09b_opinion_candidates": ("基于轮次一抽样聚类生成观点候选，供 n10 人工确认后注入轮次二；观点标签不由 AI 单方面定稿。",
                        ["skills/blueai-opinion-discovery-skill/（生成观点候选·待迁入）"], "has_asset"),
    "n10_hc_opinion": ("观点候选必须由用户确认后才注入轮次二（操作级 HC，非发布门）；候选由 n09b 生成。",
                       ["流程图 观点确认 HC"], "human"),
    "n11_annotate_r2": ("在已确认标签上标注观点类别 + UGC 参与方式 + 讨论维度（品牌/产品/活动），支撑分维度 NSR 与 UGC 分析。",
                        ["test_run/step2_round2_annotate.py", "KS_Data_Processing.md"], "has_asset"),
    "n12_aggregate": ("按「分析对象」分组输出各活动独立统计：声量趋势/互动/情感/TopN/KOL层级效能(人均互动量)/UGC分布/分维度NSR。",
                      ["skills/blueai-stats-aggregation-skill/", "KS_Metrics_Calculation.md"], "can_wire"),
    "n13_wordcloud_pull": ("逐活动拉整体/品牌/产品维度词云；competitor_sov 启用时额外拉竞品声量用于市场声量份额(SOV)。同用 SSJH skill，付费外部取数。",
                           ["skills/SSJH-Social-Stat/（付费外部·成本门后接真）", "module: wordcloud_topics / competitor_sov"], "has_asset"),
    "n14_charts": ("按图表规范生成全部图表 + 封面为本地 PNG，供报告内容层引用，并在飞书交付节点上传 media。图表(echarts)与封面能力已迁入；封面图像 API 为付费/外部服务，接真走成本确认门。",
                   ["KS_Chart_Spec.md", "skills/blueai-yuqing-echarts-skill-new/ · scripts/render_chart.js", "skills/report-cover-skill/（封面·GPT 图像 API）", "feishu.py · medias/upload_all"], "has_asset"),
    "n15_wordcloud_insight": ("词云不只展示词：三步解读(逐词查帖→剔噪音→聚 3-5 议题)，产出各平台各维度议题文字。",
                              ["skills/blueai-wordcloud-insight-skill/"], "has_asset"),
    "n16_report": ("内容层 MD（不含飞书布局），结构：数据说明→整体洞察→品牌/活动/综合/经验/建议。(report_chapters 参数控制 three vs five)",
                   ["KS_Report_Template.md", "KS_Writing_Standards.md"], "has_asset"),
    "n17_validate": ("质检发布门 G3（pass_policy=p0_count==0）。独立 topic5-validator Agent 已落地，按客户完整规则适配表执行机器全检与全文语义检查；通过后自动续跑，有 P0 自动返修，超限才升级人工。",
                     ["ma-resources-Validator/（平级资源包·契约基线，未落地）"], "human"),
    "n18_qc_gate": ("质检判定路由：无 P0→交付草稿；有 P0 且未达上限→修复复检；达上限→人工上报。",
                    ["KS_Exception_Handling.md"], "real_logic"),
    "n19_fix": ("P0 必须修复并 patch 版本递增后复检（回到 n17），避免带病交付。",
                ["KS_Exception_Handling.md"], "real_logic"),
    "n_hc7_escalate": ("质检修复超 qc_max_rounds 轮触发人工介入（操作级 HC），防止无限循环。",
                       ["流程图 HC-7"], "human"),
    "n20_delivery_draft": ("内容层 MD → 飞书布局层 XML(grid/callout)，创建 draft 文档并转移所有权（发布门 G4）。p0 禁发/p1 可披露放行。",
                           ["KS_Feishu_Format.md", "build_table_with_hc.py", "feishu-doc-writer"], "has_asset"),
    "n21_hc_user": ("用户对飞书文档终验（发布门 G4）；超 user_confirm_timeout_days 催办，再超则升级。自动流程不得替代用户确认。",
                    ["流程图 HC-T5-2 超时/催办/上报"], "human"),
    "n22_user_gate": ("终验判定路由：确认→提交；有问题→返修草稿；超时无响应→升级。",
                      ["流程图 HC-T5-2"], "real_logic"),
    "n_user_escalate": ("终验超时催办仍无响应则上报 Supervisor（操作级 HC）。",
                        ["流程图 HC-T5-2 上报"], "human"),
    "n23_delivery_commit": ("提交交付记录 + 发布后检查（图片/布局/所有权/链接/版本一致）。由控制面 Blueflare-Ada 负责；未上线时本地 stub 执行。",
                            ["Blueflare-Ada 控制面（未上线）", "release_policy.post_publish_checks"], "control_plane"),
    "n24_handoff": ("写 handoff 交回 Supervisor 并校验，闭环交付。",
                    ["validate_handoff.py / handoff.json（均缺失）"], "missing_dep"),
}

# 10 个阶段分组（用于分区展示）
STAGES = [
    ("① 准备 + 需求门 G1", ["n00_intake", "n01_hc_requirement"]),
    ("② 方案 + 方案门 G2", ["n02_plan", "n03_hc_plan", "n04_run_init"]),
    ("③ 数据获取", ["n05_brand_pull", "n06_brand_baseline", "n07_clean"]),
    ("④ AI 标注（含观点 HC）", ["n08_ugc_discovery", "n09_annotate_r1", "n09b_opinion_candidates", "n10_hc_opinion", "n11_annotate_r2"]),
    ("⑤ 统计聚合", ["n12_aggregate", "n13_wordcloud_pull"]),
    ("⑥ 图表封面", ["n14_charts"]),
    ("⑦ 报告撰写", ["n15_wordcloud_insight", "n16_report"]),
    ("⑧ 质检循环 + 验证门 G3", ["n17_validate", "n18_qc_gate", "n19_fix", "n_hc7_escalate"]),
    ("⑨ 交付草稿 + 终验门 G4", ["n20_delivery_draft", "n21_hc_user", "n22_user_gate", "n_user_escalate"]),
    ("⑩ 提交闭环", ["n23_delivery_commit", "n24_handoff"]),
]


def esc(s) -> str:
    return html.escape(str(s))


def build_mermaid(nodes: dict) -> str:
    lines = ["flowchart TD"]
    for nid, n in nodes.items():
        label = f'{nid}<br/>{n.get("name","")}'
        mode = n.get("mode")
        if mode == "decision":
            lines.append(f'  {nid}{{"{label}"}}')
        elif mode == "human":
            lines.append(f'  {nid}(["{label}"])')
        else:
            lines.append(f'  {nid}["{label}"]')
    for nid, n in nodes.items():
        if n.get("mode") == "decision":
            for route, tgt in (n.get("routes") or {}).items():
                if tgt:
                    lines.append(f'  {nid} -->|{route}| {tgt}')
        else:
            nxt = n.get("next")
            for tgt in (nxt if isinstance(nxt, list) else [nxt]):
                if tgt:
                    lines.append(f'  {nid} --> {tgt}')
    for nid in nodes:
        st = DOCS.get(nid, (None, None, "has_asset"))[2]
        lines.append(f'  style {nid} fill:{STATUS[st][2]},stroke:{STATUS[st][1]},color:#1a252f')
    return "\n".join(lines)


def node_card(nid: str, n: dict) -> str:
    biz, srcs, st = DOCS.get(nid, ("—", [], "has_asset"))
    label, scolor, sbg = STATUS[st]
    io_in = ", ".join(n.get("inputs") or []) or "—"
    io_out = ", ".join(n.get("outputs") or []) or "—"
    retry = n.get("retry") or {}
    side = n.get("side_effect", "none")
    idem = n.get("idempotency", "none")
    src_html = "".join(f"<li><code>{esc(s)}</code></li>" for s in srcs) or "<li>—</li>"
    routes = n.get("routes")
    nxt = n.get("next")
    if routes:
        flow = " · ".join(f"{k}→{v}" for k, v in routes.items())
    elif isinstance(nxt, list):
        flow = "→ [" + ", ".join(nxt) + "]"
    else:
        flow = f"→ {nxt}" if nxt else "→ 终点"
    gov_chips = ""
    if n.get("gate"):
        gov_chips += f'<span class="chip gate">gate: {esc(n["gate"])}</span>'
    if n.get("module"):
        gov_chips += f'<span class="chip mod">module: {esc(n["module"])}</span>'
    if n.get("control_plane"):
        gov_chips += f'<span class="chip cp">control-plane: {esc(n["control_plane"])}</span>'
    return f"""
    <div class="card" id="{esc(nid)}">
      <div class="card-h">
        <span class="nid">{esc(nid)}</span>
        <span class="nname">{esc(n.get('name',''))}</span>
        <span class="badge" style="color:{scolor};background:{sbg}">{esc(label)}</span>
      </div>
      <div class="meta">
        <span class="chip">owner: {esc(n.get('owner'))}</span>
        <span class="chip">mode: {esc(n.get('mode'))}</span>
        <span class="chip">retry: {esc(retry.get('max',0))}/{esc(retry.get('on_fail','-'))}</span>
        {f'<span class="chip ck">checkpoint</span>' if n.get('checkpoint') else ''}
        {gov_chips}
      </div>
      <div class="biz"><b>业务背景</b>{esc(biz)}</div>
      <div class="grid2">
        <div><b>输入</b><code>{esc(io_in)}</code></div>
        <div><b>产出</b><code>{esc(io_out)}</code></div>
      </div>
      <div><b>通过条件</b>{esc(n.get('success','—'))}</div>
      <div class="grid2">
        <div><b>副作用</b>{esc(side)}</div>
        <div><b>幂等键</b><code>{esc(idem)}</code></div>
      </div>
      <div><b>流转</b><code>{esc(flow)}</code></div>
      <div class="src"><b>实现 / 源文件映射</b><ul>{src_html}</ul></div>
    </div>"""


def governance_panel(gov: dict) -> str:
    gates = "".join(
        f"<tr><td><code>{esc(g['id'])}</code></td><td>{esc(g.get('after','—'))}</td>"
        f"<td>{esc(g.get('subject','—'))}</td><td>{esc(g.get('authority','—'))}</td>"
        f"<td>{esc(g.get('pass_policy','—'))}</td></tr>"
        for g in gov.get("release_gates", []))
    mods = gov.get("modules", {})
    core = ", ".join(mods.get("core", []))
    cond = ", ".join(mods.get("conditional", []))
    hcs = ", ".join(gov.get("operational_hcs", []))
    srcs = "".join(
        f"<tr><td><code>{esc(k)}</code></td><td>{esc(v.get('source','—'))}</td>"
        f"<td>{esc(v.get('authority','—'))}</td><td>{esc(v.get('module','—'))}</td></tr>"
        for k, v in (gov.get("data_policy", {}).get("sources", {})).items())
    fp = gov.get("failure_policy", {})
    rp = gov.get("release_policy", {})
    checks = "".join(f"<li><code>{esc(c)}</code></li>" for c in rp.get("post_publish_checks", []))
    return f"""
    <div class="gov">
      <h3 class="gh">发布门 Release Gates（authority 决定放行主体）</h3>
      <table><tr><th>门</th><th>after</th><th>subject</th><th>authority</th><th>pass_policy</th></tr>{gates}</table>
      <p class="note">操作级 HC（关卡但非发布门）：<code>{esc(hcs)}</code></p>

      <h3 class="gh">参数化模块 Modules（口径 churn 推进模块启用，不改骨架）</h3>
      <p><b>core</b><code>{esc(core)}</code></p>
      <p><b>conditional</b><code>{esc(cond)}</code></p>
      <p class="note">未登记需求策略：<code>{esc(mods.get('unregistered_request_policy','—'))}</code></p>

      <h3 class="gh">数据授权 Data Policy</h3>
      <table><tr><th>数据</th><th>source</th><th>authority</th><th>module</th></tr>{srcs}</table>
      <p class="note">冲突策略：<code>{esc(gov.get('data_policy',{}).get('conflict_policy','—'))}</code></p>

      <h3 class="gh">失败与发布策略</h3>
      <p><b>失败</b>外部 UNKNOWN=<code>{esc(fp.get('external_unknown_state','—'))}</code> ·
         幂等必需=<code>{esc(fp.get('side_effect_idempotency_required','—'))}</code> ·
         局部重跑=<code>{esc(fp.get('partial_rerun','—'))}</code> ·
         产物不覆盖=<code>{esc(fp.get('overwrite_artifact','—'))}</code></p>
      <p><b>发布</b>p0 禁发=<code>{esc(rp.get('p0_allowed','—'))}</code> ·
         p1 可披露=<code>{esc(rp.get('p1_allowed_with_disclosure','—'))}</code> ·
         渠道=<code>{esc(', '.join(rp.get('channels', [])))}</code></p>
      <div class="src"><b>发布后检查</b><ul>{checks}</ul></div>
    </div>"""


def main() -> None:
    dag = yaml.safe_load(DAG_PATH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in dag["nodes"]}
    gov = dag.get("governance", {})
    mermaid = build_mermaid(nodes)

    legend = "".join(
        f'<span class="badge" style="color:{c};background:{bg}">{esc(l)}</span>'
        for l, c, bg in STATUS.values())

    params = "".join(f"<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>"
                     for k, v in (dag.get("params") or {}).items())

    sections = ""
    for stage_name, ids in STAGES:
        cards = "".join(node_card(i, nodes[i]) for i in ids if i in nodes)
        sections += f'<h3 class="stage">{esc(stage_name)}</h3>{cards}'

    diff = """
    <ul>
      <li><b>并行化（本轮）</b>：调度由单指针改为<b>就绪队列(frontier)</b>——<code>next</code> 支持列表(分叉)，
          节点 <code>inputs</code> 全部就绪才可跑(汇合)。三处天然并行如实表达：
          <code>n04→[n05,n06,n13]</code> 三路数说、<code>n07→[n08,n09]</code>、<code>n13→[n14,n15]</code>；
          join 节点 <code>n11/n12/n16</code> 在各自上游齐备后才运行。执行仍串行确定性，仅留一处真并发接缝；
          QC 环靠 control 边驱动不受影响。n08/n09b/n15/n16 的 owner 改为对应语义子 Agent
          （Discovery/Opinion/Insight/Narrative，身份文件见 <code>identity/subagents/</code>）。</li>
      <li><b>相对 v0.1 的改进</b>：补回方案确认门 G2（<code>n02_plan→n03_hc_plan</code>）、
          保留操作级 HC 与 QC 循环（<code>n17→n18→n19</code> 回环）、补终验超时升级边
          （<code>n22_user_gate→n_user_escalate</code>）、交付 draft→commit 分离
          （<code>n20_delivery_draft</code> / <code>n23_delivery_commit</code>）、具体幂等键、本地可跑。</li>
      <li><b>已迁入</b>：<code>skills/SSJH-Social-Stat/</code>（n05/n06/n13）、<code>skills/blueai-yuqing-echarts-skill-new/</code>（n14 图表）、
          <code>skills/report-cover-skill/</code>（n14 封面）。SSJH / 图像 API 为<b>付费或外部调用</b>，接真须走成本确认门；
          图表为本地 PNG，由 n20 上传飞书 media。</li>
      <li><b>仍缺依赖</b>：<code>validate_handoff.py / handoff.json</code> — 补齐前 <code>n24</code> 仍只能 stub。</li>
      <li><b>控制面未上线</b>：Blueflare-Ada 未上线，<code>n04_run_init</code> / <code>n23_delivery_commit</code>
          以本地 stub 执行，保留接入缝。</li>
      <li><b>可立即接真</b>：<code>n07_clean</code>、<code>n12_aggregate</code>（本地已有脚本，不依赖外部服务）。</li>
    </ul>"""

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(OVERVIEW['title'])}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
:root{{--bd:#e3e6ea;--mut:#5b6672;--bg:#f6f8fa}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a252f;background:var(--bg)}}
.wrap{{max-width:1120px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:26px;margin:0 0 4px}}
.sub{{color:var(--mut);margin:0 0 20px}}
.panel{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:20px 22px;margin:16px 0}}
.panel h2{{font-size:18px;margin:0 0 12px}}
.ov p{{margin:6px 0}} .ov b{{color:#1f5fb0}}
.legend{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}}
.badge{{font-size:12px;padding:2px 9px;border-radius:20px;font-weight:600;white-space:nowrap}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:var(--mut);font-weight:600;border-bottom:2px solid var(--bd);padding:6px 8px}}
td{{border-top:1px solid var(--bd);padding:6px 8px;vertical-align:top}}
.stage{{margin:28px 0 6px;font-size:16px;color:#2c3e50;border-left:4px solid #1f5fb0;padding-left:10px}}
.gov .gh{{font-size:15px;color:#2c3e50;margin:18px 0 6px}}
.gov p{{margin:5px 0}}
.card{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin:12px 0;scroll-margin-top:16px}}
.card-h{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}}
.nid{{font:600 13px/1 ui-monospace,monospace;background:#eef2f6;color:#334;padding:4px 8px;border-radius:6px}}
.nname{{font-weight:700;font-size:16px}}
.meta{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}}
.chip{{font-size:12px;background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:2px 8px;color:var(--mut)}}
.chip.ck{{color:#1a7f4b;border-color:#bfe6cd;background:#eefaf1}}
.chip.gate{{color:#b02a2a;border-color:#f0c4c4;background:#fdeaea;font-weight:600}}
.chip.mod{{color:#1f5fb0;border-color:#c4d6f0;background:#e7f0fb}}
.chip.cp{{color:#0f766e;border-color:#b6ddd7;background:#e6f5f3}}
.card>div{{margin:7px 0}}
.card b{{display:inline-block;min-width:92px;color:#2c3e50;margin-right:6px}}
.biz{{background:#fafbfc;border:1px dashed var(--bd);border-radius:8px;padding:8px 12px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
code{{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:#f2f4f7;padding:1px 6px;border-radius:5px;word-break:break-all}}
.src ul{{margin:4px 0 0;padding-left:20px}} .src li{{margin:3px 0}}
.mermaid{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px;overflow:auto}}
.note{{color:var(--mut);font-size:13px}}
</style></head>
<body><div class="wrap">
<h1>{esc(OVERVIEW['title'])}</h1>
<p class="sub">机器可读 DAG 的发布视图 · 结构实时取自 pipeline.v0.2.yaml（v{esc(dag.get('version'))}，{len(nodes)} 节点）· 治理层取自 governance 段 · 业务背景与源文件为人工标注</p>

<div class="panel ov">
  <h2>业务背景</h2>
  <p><b>它解决什么</b>{esc(OVERVIEW['background'])}</p>
  <p><b>典型场景</b>{esc(OVERVIEW['scene'])}</p>
  <p><b>客户</b>{esc(OVERVIEW['client'])}</p>
  <p><b>交付价值</b>{esc(OVERVIEW['value'])}</p>
  <p class="note">{esc(OVERVIEW['source'])}</p>
  <div class="legend">{legend}</div>
</div>

<div class="panel"><h2>流程全景</h2><div class="mermaid">{mermaid}</div></div>

<div class="panel"><h2>治理层 Governance（声明式约束）</h2>{governance_panel(gov)}</div>

<div class="panel"><h2>业务口径参数（外置，不写进节点逻辑）</h2><table>{params}</table></div>

<div class="panel"><h2>差异与依赖说明</h2>{diff}</div>

<h2 style="margin:28px 0 0">节点明细（实现 + 业务背景）</h2>
{sections}

</div>
<script>mermaid.initialize({{startOnLoad:true,theme:'neutral',flowchart:{{useMaxWidth:true}}}});</script>
</body></html>"""

    OUT_PATH.write_text(doc, encoding="utf-8")
    print(f"written: {OUT_PATH.resolve()}  ({len(doc)} chars, {len(nodes)} nodes)")


if __name__ == "__main__":
    main()
