"""HC（Human-in-the-loop）桥接层 — Gateway ↔ 编排引擎 的人工关卡契约。

薄 Agent + Durable Workflow 下，人工关卡的闭环是：
  引擎 HumanPause → 写 hc_pending.json（薄 Agent 据此输出 HC 卡片 JSON 结束 turn）
  → Gateway 落 human_tasks 发卡 → 用户点卡/回消息（HCx [pass/reject/remark]: note）
  → Gateway resume_job 注入结构化决定 → 引擎 resume 续跑。

本模块把「暂停节点 + 用户决定」翻译成引擎 resume 所需的 human_outputs，不改 DAG schema。
决定格式（project_memory user_confirmed）：严格 `HCx [pass/reject/remark]: [note]`。
remark 无内容只记录、不触发续跑（等待后续消息）；remark 带 note 视为追加说明后续跑。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import feishu
# 人工关卡 node_id → HC 种类（供卡片展示 / resume --hc 定位）。
NODE_TO_HC = {
    "n01_hc_requirement": "G1",
    "n03_hc_plan": "G2",
    "n10_hc_opinion": "N10",
    "n17_validate": "G3",
    "n_hc7_escalate": "HC7",
    "n21_hc_user": "G4",
    "n_user_escalate": "USER_ESCALATE",
}
HC_TO_NODE = {v: k for k, v in NODE_TO_HC.items()}

# 决定关键词 → 规范化。
_DECISION_RE = re.compile(
    r"^\s*(?:HC\w+|G\d)?\s*\[?\s*(pass|reject|remark|confirmed|issues|escalate)\s*\]?\s*[:：]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
_G1_REQUIRED = (
    "brand",
    "activity_name",
    "period_start",
    "period_end",
    "platforms",
    "brand_query",
    "activity_query",
    "content_scope",
    "business_questions",
    "delivery_expectation",
    "allowed_actions",
)


def parse_decision(text: str) -> tuple[str, str]:
    """解析 `HCx [pass/reject/remark]: note` → (decision, note)。非法格式回退 remark。"""
    m = _DECISION_RE.match(text or "")
    if not m:
        return "remark", (text or "").strip()
    return m.group(1).lower(), (m.group(2) or "").strip()


def hc_kind_for_node(node_id: str) -> str:
    return NODE_TO_HC.get(node_id, node_id)


def _is_external_permission_p0(issues: list[Any]) -> bool:
    return any(
        isinstance(issue, dict)
        and issue.get("severity") == "P0"
        and (
            issue.get("locus") == "feishu-permission"
            or issue.get("id") == "P0-postpublish-owner"
        )
        for issue in issues
    )


def write_hc_pending(st, run_dir: Path) -> dict:
    """引擎暂停在人工关卡时，落 hc_pending.json 供薄 Agent 产卡。"""
    human = st.human or {}
    node_id = human.get("node", "")
    payload = {
        "run_id": st.run_id,
        "task_id": st.task_id,
        "node": node_id,
        "hc_kind": hc_kind_for_node(node_id),
        "prompt": human.get("prompt"),
        "expect": human.get("expect", []),
        "status": "awaiting_human",
    }
    if node_id == "n03_hc_plan":
        payload["review_context"] = {
            "analysis_plan": st.read_artifact("analysis_plan") or {},
            "execution_plan": st.read_artifact("execution_plan") or {},
            "selected_modules": st.read_artifact("selected_modules") or {},
            "artifact_refs": {
                key: st.artifact_ref(key)
                for key in ("analysis_plan", "execution_plan", "selected_modules")
            },
        }
    elif node_id == "n10_hc_opinion":
        payload["review_context"] = {
            "opinion_candidates": st.read_artifact("opinion_candidates") or {},
            "ugc_category_list": st.read_artifact("ugc_category_list") or {},
            "artifact_refs": {
                key: st.artifact_ref(key)
                for key in ("opinion_candidates", "ugc_category_list")
            },
        }
    elif node_id == "n17_validate":
        qc_path = Path(run_dir) / "validation" / "qc_report.json"
        payload["review_context"] = {
            "report_ref": st.artifact_ref("report_md"),
            "qc_report_path": str(qc_path),
            "qc_report": (
                json.loads(qc_path.read_text(encoding="utf-8"))
                if qc_path.exists() else None
            ),
        }
    elif node_id == "n_hc7_escalate":
        qc_report = st.read_artifact("qc_report") or {}
        delivery_draft = st.read_artifact("delivery_draft") or {}
        post_publish = (
            delivery_draft.get("post_publish_validation")
            if isinstance(delivery_draft, dict) else {}
        ) or {}
        issues = list(post_publish.get("issues") or [])
        if not issues and isinstance(qc_report, dict):
            issues = list(qc_report.get("issues") or [])
        payload["review_context"] = {
            "escalation_reason": (
                "external_permission"
                if _is_external_permission_p0(issues)
                else "qc_retry_exhausted"
            ),
            "issues": issues,
            "qc_report": qc_report,
            "delivery_draft": delivery_draft,
            "qc_report_ref": st.artifact_ref("qc_report"),
            "delivery_ref": st.artifact_ref("delivery_draft"),
            "report_ref": st.artifact_ref("report_md"),
        }
    elif node_id == "n21_hc_user":
        payload["review_context"] = {
            "delivery_draft": st.read_artifact("delivery_draft") or {},
            "delivery_ref": st.artifact_ref("delivery_draft"),
            "report_ref": st.artifact_ref("report_md"),
        }
    p = Path(run_dir) / "hc_pending.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def clear_hc_pending(run_dir: Path) -> None:
    p = Path(run_dir) / "hc_pending.json"
    if p.exists():
        p.unlink()


def _parse_requirement(note: str) -> dict:
    """解析并校验 G1 表单 JSON，防止跨 Host/Agent 边界时字段丢失。"""
    raw = note or ""
    if raw.startswith("@"):
        path = Path(raw[1:])
        upload_root = Path("/mnt/session/uploads")
        if (
            path.parent != upload_root
            or path.name != str(path).split("/")[-1]
            or not path.name.startswith("topic5_requirement_")
            or path.suffix.lower() != ".json"
        ):
            raise ValueError("G1 需求文件路径非法")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("G1 需求文件无法读取") from error
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("G1 需求载荷不是有效 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("G1 需求载荷必须是 JSON 对象")
    missing = [key for key in _G1_REQUIRED if not value.get(key)]
    if missing:
        raise ValueError("G1 需求缺少必填字段: " + ", ".join(missing))
    for field in ("period_start", "period_end"):
        text = str(value[field])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError(f"G1 {field} 必须是 YYYY-MM-DD")
        try:
            date.fromisoformat(text)
        except ValueError as error:
            raise ValueError(f"G1 {field} 不是有效日期") from error
    if str(value["period_start"]) > str(value["period_end"]):
        raise ValueError("G1 活动开始日期不能晚于结束日期")
    if value.get("content_scope") not in ("all", "ugc", "pgc"):
        raise ValueError("G1 content_scope 必须是 all/ugc/pgc")
    return value


def _parse_source_file(note: str) -> dict:
    try:
        value = json.loads(note or "")
    except (TypeError, ValueError) as error:
        raise ValueError("G2 通过后缺少活动明细文件引用") from error
    source = value.get("source_file") if isinstance(value, dict) else None
    if not isinstance(source, dict) or not source.get("mount_path"):
        raise ValueError("G2 通过后缺少活动明细文件引用")
    mount_path = str(source["mount_path"])
    if Path(mount_path).name != mount_path or not mount_path.lower().endswith(".xlsx"):
        raise ValueError("G2 source_file.mount_path 非法")
    return {
        "name": str(source.get("name") or mount_path),
        "mount_path": mount_path,
    }


def build_human_outputs(
    st,
    decision: str,
    note: str = "",
    requested_hc: str | None = None,
) -> Optional[dict]:
    """把用户决定翻译成当前暂停节点所需的 human_outputs。

    返回 None 表示「只记录 remark、不续跑」（remark 无内容，等待后续消息指令）。
    """
    human = st.human or {}
    node_id = human.get("node", "")
    expect = human.get("expect", []) or []
    decision = (decision or "").lower()
    current_hc = hc_kind_for_node(node_id)
    if requested_hc and requested_hc != current_hc:
        raise ValueError(
            f"过期 HC 回注：请求 {requested_hc}，当前等待 {current_hc}"
        )

    # remark 无内容：只记录，不触发续跑（project_memory 硬约束）。
    if decision == "remark" and not note.strip():
        return None

    # n17_validate: only an independently persisted Validator result may resume G3.
    if node_id == "n17_validate":
        qc_path = st.run_dir / "validation" / "qc_report.json"
        if not qc_path.exists():
            raise ValueError("G3 缺少独立 Validator 产出的 validation/qc_report.json")
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        report_ref = st.artifact_ref("report_md")
        if not isinstance(qc, dict) or qc.get("validator") != "topic5-validator":
            raise ValueError("G3 qc_report 缺少独立 Validator 身份")
        if qc.get("subject") != report_ref:
            raise ValueError("G3 qc_report 绑定的 ReportArtifact 不是当前报告版本")
        metadata = qc.get("qc_metadata") or {}
        input_keys = {
            item.get("key")
            for item in (metadata.get("input_artifacts") or [])
            if isinstance(item, dict) and item.get("sha256")
        }
        execution_ref = qc.get("execution_ref") or {}
        if (
            not qc.get("execution_source")
            or metadata.get("mode") != "independent-agent"
            or metadata.get("validator_id") != "topic5-validator"
            or not qc.get("rules_version")
            or not qc.get("validator_version")
            or not all(
                execution_ref.get(key)
                for key in ("task_id", "run_id", "attempt_id")
            )
            or not {
                "report_md", "requirement_confirmed", "stats_bundle",
                "chart_urls", "validator_rules",
            }.issubset(input_keys)
        ):
            raise ValueError(
                "G3 qc_report 缺少独立执行来源、资源版本、执行引用或完整输入绑定"
            )
        if bool(qc.get("has_p0")) != (int(qc.get("p0_count") or 0) > 0):
            raise ValueError("G3 qc_report 的 has_p0 与 p0_count 不一致")
        if decision in ("pass", "confirmed") and qc.get("has_p0"):
            raise ValueError("G3 当前 Validator 结果仍有 P0，禁止通过")
        # reject：已有 Validator P0 时直接采用其结果；仅在无 P0 时视为人工强制打回。
        if decision == "reject" and not qc.get("has_p0"):
            qc = dict(qc)
            qc["issues"] = list(qc.get("issues", [])) + [{
                "id": "P0-human-reject", "severity": "P0",
                "title": "人工质检打回", "evidence": note or "人工判定需返修",
                "locus": "human"}]
            qc["has_p0"] = True
            qc["p0_count"] = qc.get("p0_count", 0) + 1
        if note:
            qc = dict(qc)
            qc["human_note"] = note
        return {"qc_report": qc}

    # n21_hc_user（G4 用户终验）：pass→confirmed；reject→issues（回 n20 返修）；escalate→escalate。
    if node_id == "n21_hc_user":
        status = {"pass": "confirmed", "reject": "issues",
                  "escalate": "escalate", "confirmed": "confirmed",
                  "issues": "issues"}.get(decision)
        if status is None:
            raise ValueError(f"G4 不支持决定 {decision!r}")
        rep = st.read_artifact("report_md") or {}
        draft = st.read_artifact("delivery_draft") or {}
        confirmation = {
            "status": status,
            "report_version": rep.get("version") if isinstance(rep, dict) else None,
            "report_ref": st.artifact_ref("report_md"),
            "delivery_ref": st.artifact_ref("delivery_draft"),
            "revision_id": draft.get("revision_id") if isinstance(draft, dict) else None,
            "note": note,
        }
        if status == "confirmed":
            post_publish = (
                draft.get("post_publish_validation")
                if isinstance(draft, dict) else {}
            ) or {}
            if (
                not confirmation["revision_id"]
                or (isinstance(draft, dict) and draft.get("state") != "ready")
                or post_publish.get("has_p0")
            ):
                raise ValueError(
                    "G4 交付草稿未 ready、未绑定 revision 或发布后 Validator 仍有 P0"
                )
            if not draft.get("degraded"):
                live_revision = feishu.current_revision(str(draft.get("doc_token") or ""))
                if live_revision is None:
                    raise ValueError("G4 无法复读当前飞书 revision，禁止通过")
                if live_revision != confirmation["revision_id"]:
                    raise ValueError("G4 飞书文档已变化，需重新回读、质检并生成新终验对象")
            return {"user_confirmation": confirmation}
        if status == "issues":
            prior_qc = st.read_artifact("qc_report") or {}
            qc = dict(prior_qc) if isinstance(prior_qc, dict) else {}
            qc["issues"] = list(qc.get("issues") or []) + [{
                "id": "P0-g4-user-reject",
                "severity": "P0",
                "title": "用户终验退回",
                "evidence": note or "用户要求修订最终交付",
                "locus": "G4",
            }]
            qc["has_p0"] = True
            qc["p0_count"] = int(qc.get("p0_count") or 0) + 1
            return {
                "user_confirmation": confirmation,
                "qc_report": qc,
                "_control": {
                    "next": "n19_fix",
                    "gate_status": "rejected",
                    "invalidate_artifacts": [
                        "delivery_draft", "external_receipts", "feishu_doc",
                        "delivery_record", "task_result", "handoff",
                    ],
                },
            }
        return {"user_confirmation": confirmation}

    # HC7 never waives P0. A positive decision means another repair/review round.
    if node_id == "n_hc7_escalate":
        target = node_id
        if decision in ("pass", "confirmed"):
            draft = st.read_artifact("delivery_draft") or {}
            post_publish = (
                draft.get("post_publish_validation")
                if isinstance(draft, dict) else {}
            ) or {}
            issues = list(post_publish.get("issues") or [])
            if not issues:
                qc = st.read_artifact("qc_report") or {}
                if isinstance(qc, dict):
                    issues = list(qc.get("issues") or [])
            target = (
                "n20_delivery_draft"
                if _is_external_permission_p0(issues)
                else "n19_fix"
            )
        return {
            expect[0] if expect else "escalation_decision": {
                "decision": decision,
                "note": note,
                "escalated": True,
                "release_authorized": False,
            },
            "_control": {
                "next": target,
                "gate_status": "escalated",
            },
        }

    # G4 timeout escalation cannot substitute for the user's revision-bound acceptance.
    if node_id == "n_user_escalate":
        return {
            expect[0] if expect else "escalation_decision": {
                "decision": decision,
                "note": note,
                "escalated": True,
                "release_authorized": False,
            },
            "_control": {
                "next": "n21_hc_user",
                "gate_status": "escalated",
            },
        }

    # 需求确认 G1 / 方案确认 G2：pass 产确认产物；reject 视为需补充（note 必带）。
    if node_id == "n01_hc_requirement":
        if decision not in ("pass", "confirmed"):
            return {
                "requirement_confirmed": {"confirmed": False, "note": note},
                "_control": {
                    "next": "n00_intake",
                    "gate_status": "rejected",
                    "invalidate_artifacts": ["requirement_confirmed"],
                },
            }
        requirement = _parse_requirement(note)
        return {"requirement_confirmed": {
            **requirement,
            "confirmed": True,
            "validator_precheck": {
                "status": "passed",
                "rules": [
                    "required_fields", "date_range", "content_scope",
                ],
                "authority": "user",
            },
        }}
    if node_id == "n03_hc_plan":
        requirement = st.read_artifact("requirement_confirmed") or {}
        analysis_plan = st.read_artifact("analysis_plan") or {}
        execution_plan = st.read_artifact("execution_plan") or {}
        selected_modules = st.read_artifact("selected_modules") or {}
        refs = {
            key: st.artifact_ref(key)
            for key in ("analysis_plan", "execution_plan", "selected_modules")
        }
        plan_hashes = {
            value.get("plan_bundle_sha256")
            for value in (analysis_plan, execution_plan, selected_modules)
            if isinstance(value, dict)
        }
        if None in plan_hashes or len(plan_hashes) != 1:
            raise ValueError("G2 方案 Artifact 的 bundle hash 不一致")
        confirmed = decision in ("pass", "confirmed")
        source_file = {}
        if confirmed:
            source_file = (
                _parse_source_file(note)
                if note
                else (
                    requirement.get("source_file")
                    if isinstance(requirement.get("source_file"), dict)
                    else {}
                )
            )
            if not source_file:
                raise ValueError("G2 通过后缺少活动明细文件引用")
        plan = {
            **(requirement if isinstance(requirement, dict) else {}),
            **({"source_file": source_file} if source_file else {}),
            "analysis_plan": analysis_plan,
            "execution_plan": execution_plan,
            "selected_modules": selected_modules,
            "confirmed": confirmed,
            "version": f"plan-v{refs['analysis_plan']['version']}",
            "subject": refs,
            "plan_bundle_sha256": next(iter(plan_hashes)),
            "scope": analysis_plan.get("scope") if isinstance(analysis_plan, dict) else {},
            "note": note,
            "validator_precheck": {
                "status": (
                    "passed"
                    if (execution_plan.get("preflight") or {}).get("status")
                    in ("READY", "READY_PENDING_INPUT")
                    else "failed"
                ) if isinstance(execution_plan, dict) else "failed",
                "rules": [
                    "plan_bundle_hash", "capability_registry",
                    "input_schema", "metric_policy",
                ],
                "authority": "user",
            },
        }
        if confirmed:
            preflight = execution_plan.get("preflight") if isinstance(execution_plan, dict) else {}
            if (preflight or {}).get("status") not in (
                "READY",
                "READY_PENDING_INPUT",
            ):
                raise ValueError("G2 能力/权限/输入预检未通过，禁止进入生产 Run")
            return {"confirmed_plan": plan}
        return {
            "confirmed_plan": plan,
            "_control": {
                "next": "n02_plan",
                "gate_status": "rejected",
                "invalidate_artifacts": [
                    "run_record", "resource_lock", "context_snapshot",
                ],
            },
        }

    # N10 jointly confirms opinion and UGC labels against exact candidate versions.
    if node_id == "n10_hc_opinion":
        prev = st.read_artifact("opinion_candidates") or {}
        labels = (prev.get("candidates") or prev.get("labels")
                  if isinstance(prev, dict) else None) or []
        ugc = st.read_artifact("ugc_category_list") or {}
        ugc_categories = (
            ugc.get("categories") if isinstance(ugc, dict) else None
        ) or []
        extra = [x.strip() for x in re.split(r"[,，、]", note) if x.strip()] if note else []
        confirmed = decision in ("pass", "confirmed")
        result = {
            "labels": list(labels) + (extra if confirmed else []),
            "ugc_categories": list(ugc_categories),
            "ugc_not_applicable": bool(
                isinstance(ugc, dict) and ugc.get("not_applicable")
            ),
            "confirmed": confirmed,
            "note": note,
            "subject": {
                "opinion_candidates": st.artifact_ref("opinion_candidates"),
                "ugc_category_list": st.artifact_ref("ugc_category_list"),
            },
        }
        if confirmed:
            return {"confirmed_opinion_labels": result}
        return {
            "confirmed_opinion_labels": result,
            "_control": {
                "next": ["n08_ugc_discovery", "n09b_opinion_candidates"],
                "gate_status": "rejected",
                "invalidate_artifacts": [
                    "confirmed_opinion_labels", "annotated_data", "stats_bundle",
                    "chart_urls", "wordcloud_insight", "report_md", "qc_report",
                    "delivery_draft", "external_receipts", "feishu_doc",
                    "user_confirmation", "delivery_record", "task_result", "handoff",
                ],
            },
        }

    # 兜底：按 expect 产最小确认产物。
    return {k: {"confirmed": decision in ("pass", "confirmed"), "note": note}
            for k in (expect or ["confirmed"])}
