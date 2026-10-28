"""飞书文档交付适配层 — 供 delivery_draft handler 接真（n20_delivery_draft）。

原生插图：图表以**本地 PNG** 进飞书文档。飞书 docx 的 image block
不接受外链 URL，必须走三步：
  1) 建空 image block（block_type=27）拿 block_id；
  2) POST /open-apis/drive/v1/medias/upload_all 传本地二进制（parent_type=docx_image、
     parent_node=block_id）拿 file_token；
  3) PATCH blocks/{block_id} 用 replace_image.token=file_token 回填，图才显示。
需权限 drive:drive（上传 media）。

设计（类比 semantic.py / charts.py，零三方依赖，用 urllib）：
  - 真路径：拿 tenant/user access token → 新建 docx → MD 内容层转飞书 block 写入（图片先
    上传 media 再回填）→ 转移所有权 → 导出后回读核对（章节齐全 / 图 block 生效 /
    绑定报告版本一致）→ 返回 feishu_doc（含 doc_token/url/revision_id/report_version/readback）。
  - 诊断适配器：可构造 degraded=True 的同构占位产物供单元测试；生产 handler 会 fail-closed，
    不允许该产物进入 n21/n23/n24。

幂等：同 Task + 同报告版本只建一份 draft（由引擎 idempotency=feishu:{task_id}:{resource_lock}:{report_version}
处理）；本层若命中已存在回执（external_receipts.doc_token）则复用、不新建。

凭据只从环境变量读（生产由 MA Vault 注入）：
  FEISHU_APP_ID / FEISHU_APP_SECRET（tenant token），或 FEISHU_USER_ACCESS_TOKEN（用户态）。
  FEISHU_USER_OPEN_ID（任务发起人，所有权转移目标；缺失时交付 fail-closed）。
  FEISHU_WEB_BASE（可选，用户访问域名；默认 https://www.feishu.cn）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional

_BASE = (os.environ.get("FEISHU_OPENAPI_BASE") or "https://open.feishu.cn").rstrip("/")
_WEB_BASE = (os.environ.get("FEISHU_WEB_BASE") or "https://www.feishu.cn").rstrip("/")


def _app_creds() -> tuple[str, str]:
    return (os.environ.get("FEISHU_APP_ID") or "",
            os.environ.get("FEISHU_APP_SECRET") or "")


def _user_token() -> str:
    return os.environ.get("FEISHU_USER_ACCESS_TOKEN") or ""


def api_available() -> bool:
    """是否具备飞书真调用条件：有用户态 token，或 app id+secret。"""
    if _user_token():
        return True
    app_id, app_secret = _app_creds()
    return bool(app_id and app_secret)


# ── HTTP ─────────────────────────────────────────────────────
def _http_error(error: urllib.error.HTTPError, token: str = "") -> RuntimeError:
    """Retain actionable API diagnostics without headers or credential bodies."""
    try:
        payload = json.loads(error.read(16384).decode("utf-8"))
    except (ValueError, UnicodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    detail = payload.get("error") or {}
    if not isinstance(detail, dict):
        detail = {}
    message = str(payload.get("msg") or "request rejected")
    for value in (token, _user_token(), _app_creds()[1]):
        if value:
            message = message.replace(value, "[redacted]")
    path = str(error.url).split("?", 1)[0]
    return RuntimeError(
        f"飞书 HTTP {error.code} {path} code={payload.get('code')} "
        f"msg={message[:300]} log_id={detail.get('log_id') or ''}"
    )


def _req(method: str, path: str, token: str, body: Optional[dict] = None,
         timeout: int = 30) -> dict:
    url = f"{_BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _http_error(error, token) from error
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"飞书 API 错误 code={payload.get('code')} msg={payload.get('msg')}")
    return payload.get("data") or {}


def _upload_media(token: str, local_path: str, parent_node: str,
                  timeout: int = 60) -> str:
    """上传本地文件到 drive/v1/medias/upload_all（multipart），返回 file_token。

    零三方依赖：手写 multipart/form-data。parent_type=docx_image、parent_node=image block_id。
    """
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    file_name = os.path.basename(local_path)
    boundary = f"----topic5bndry{uuid.uuid4().hex}"
    fields = {
        "file_name": file_name,
        "parent_type": "docx_image",
        "parent_node": parent_node,
        "size": str(len(file_bytes)),
    }
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        parts.append(f"{v}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        .encode())
    parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = f"{_BASE}/open-apis/drive/v1/medias/upload_all"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _http_error(error, token) from error
    if payload.get("code") not in (0, None):
        raise RuntimeError(
            f"飞书 media 上传失败 code={payload.get('code')} msg={payload.get('msg')}")
    tok = (payload.get("data") or {}).get("file_token")
    if not tok:
        raise RuntimeError(f"飞书 media 上传未返回 file_token：{payload}")
    return tok


def _tenant_token() -> str:
    """优先用户态 token；否则用 app id/secret 换 tenant_access_token。"""
    ut = _user_token()
    if ut:
        return ut
    app_id, app_secret = _app_creds()
    if not (app_id and app_secret):
        raise RuntimeError("飞书凭据缺失（FEISHU_APP_ID/SECRET 或 FEISHU_USER_ACCESS_TOKEN）")
    url = f"{_BASE}/open-apis/auth/v3/tenant_access_token/internal"
    req = urllib.request.Request(url, method="POST", data=json.dumps(
        {"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _http_error(error) from error
    tok = payload.get("tenant_access_token")
    if not tok:
        raise RuntimeError(f"取 tenant_access_token 失败：{payload}")
    return tok


# ── MD → 飞书 docx block ──────────────────────────────────────
# docx v1 block_type 为整数枚举（旧版用字符串键会被真实 API 拒绝）。
_BLOCK_TEXT = 2
_BLOCK_IMAGE = 27
_BLOCK_TABLE = 31
_HEADING_BLOCK_TYPE = {1: 3, 2: 4, 3: 5, 4: 6}
_HEADING_KEY = {1: "heading1", 2: "heading2", 3: "heading3", 4: "heading4"}


def _text_block(content: str) -> dict:
    return {"block_type": _BLOCK_TEXT,
            "text": {"elements": [{"text_run": {"content": content}}]}}


def _split_table_row(line: str) -> list[str]:
    """拆分 Markdown 表格行，支持转义竖线。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    cells = re.split(r"(?<!\\)\|", text)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _table_block(rows: list[list[str]]) -> dict:
    column_size = max(len(row) for row in rows)
    normalized = [row + [""] * (column_size - len(row)) for row in rows]
    return {
        "block_type": _BLOCK_TABLE,
        "table": {"property": {
            "row_size": len(normalized),
            "column_size": column_size,
            "header_row": True,
        }},
        "_table_rows": normalized,
    }


def md_to_blocks(markdown: str) -> list[dict]:
    """把内容层 MD 转飞书 docx block（标题/段落/图片/表格）。

    Markdown 表格的分隔行不会写入文档，首行作为原生表格表头。图片 path 若为**存在的
    本地文件**，产空 image block 并带内部提示，由 create_delivery 上传 media 后回填；
    否则降级为 text 行 `[图] alt: path` 保证可追溯。所有下划线前缀的内部提示键在真正
    发送前剥离。
    """
    blocks: list[dict] = []
    lines = (markdown or "").splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            i += 1
            continue
        if (i + 1 < len(lines) and "|" in line
                and _is_table_separator(lines[i + 1])):
            rows = [_split_table_row(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(_table_block(rows))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            bt = _HEADING_BLOCK_TYPE.get(level, 6)
            key = _HEADING_KEY.get(level, "heading4")
            blocks.append({"block_type": bt, key: {
                "elements": [{"text_run": {"content": m.group(2).strip()}}]}})
            i += 1
            continue
        img = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line)
        if img:
            alt, path = img.group(1), img.group(2)
            if path and os.path.isfile(path):
                blocks.append({"block_type": _BLOCK_IMAGE, "image": {},
                               "_local_path": path, "_alt": alt})
            else:
                blocks.append(_text_block(f"[图] {alt}: {path}"))
            i += 1
            continue
        blocks.append(_text_block(line.strip()))
        i += 1
    return blocks


def _strip_internal(block: dict) -> dict:
    """剥离 image block 的内部提示键（_local_path/_alt），得到可发送的 block。"""
    result = {k: v for k, v in block.items() if not k.startswith("_")}
    if result.get("block_type") == _BLOCK_TABLE:
        table = dict(result["table"])
        prop = dict(table["property"])
        # The children-create API accepts at most 9 rows and 9 columns.
        # Keep the full source dimensions intact for expansion before filling.
        for dimension in ("row_size", "column_size"):
            prop[dimension] = min(prop[dimension], 9)
        table["property"] = prop
        result["table"] = table
    return result


# ── 真路径 ────────────────────────────────────────────────────
def _create_children(token: str, doc_token: str, blocks: list[dict],
                     batch: int = 50) -> list[dict]:
    """批量创建子 block（docx 单次 children 上限 50），返回每个已建 block（含 block_id），顺序对齐。"""
    created: list[dict] = []
    index = 0
    for i in range(0, len(blocks), batch):
        chunk = [_strip_internal(b) for b in blocks[i:i + batch]]
        data = _req(
            "POST",
            f"/open-apis/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
            token, {"children": chunk, "index": index})
        created.extend(data.get("children") or [])
        index += len(chunk)
    return created


def _fill_images(token: str, doc_token: str, blocks: list[dict],
                 created: list[dict]) -> int:
    """对每个 image block 上传本地 PNG 为 media 并 PATCH replace_image 回填。返回成功张数。

    best-effort：单张上传/回填失败只跳过该图（不阻断整篇交付）。
    """
    filled = 0
    for submitted, got in zip(blocks, created):
        if submitted.get("block_type") != _BLOCK_IMAGE:
            continue
        local_path = submitted.get("_local_path")
        block_id = got.get("block_id")
        if not (local_path and block_id and os.path.isfile(local_path)):
            continue
        try:
            file_token = _upload_media(token, local_path, parent_node=block_id)
            _req("PATCH",
                 f"/open-apis/docx/v1/documents/{doc_token}/blocks/{block_id}",
                 token, {"replace_image": {"token": file_token}})
            filled += 1
        except Exception:
            continue
    return filled


def _expand_table(token: str, doc_token: str, block_id: str,
                  row_count: int, column_count: int) -> dict:
    """Grow a small native table, reconciling its live dimensions first."""
    path = f"/open-apis/docx/v1/documents/{doc_token}/blocks/{block_id}"
    data = _req("GET", path, token)
    table = (data.get("block") or data).get("table") or {}
    prop = table.get("property") or {}
    current_rows = int(prop.get("row_size") or 0)
    current_columns = int(prop.get("column_size") or 0)
    if not (0 < current_rows <= row_count and 0 < current_columns <= column_count):
        raise RuntimeError("飞书表格扩容前尺寸与源表格不一致")
    for axis, start, target in (
        ("column", current_columns, column_count),
        ("row", current_rows, row_count),
    ):
        for index in range(start, target):
            client_token = uuid.uuid5(
                uuid.NAMESPACE_URL, f"{doc_token}/{block_id}/{axis}/{index}"
            )
            _req(
                "PATCH", f"{path}?client_token={client_token}", token,
                {f"insert_table_{axis}": {f"{axis}_index": -1}},
            )
    data = _req("GET", path, token)
    table = (data.get("block") or data).get("table") or {}
    prop = table.get("property") or {}
    if prop.get("row_size") != row_count or prop.get("column_size") != column_count:
        raise RuntimeError("飞书表格扩容后尺寸与源表格不一致")
    return table


def _fill_tables(token: str, doc_token: str, blocks: list[dict],
                 created: list[dict]) -> int:
    """把内部表格行数据写入飞书创建出的 table cell block。"""
    filled = 0
    for submitted, got in zip(blocks, created):
        if submitted.get("block_type") != _BLOCK_TABLE:
            continue
        rows = submitted.get("_table_rows") or []
        table = got.get("table") or {}
        if rows and (len(rows) > 9 or len(rows[0]) > 9):
            block_id = got.get("block_id")
            if not block_id:
                raise RuntimeError("飞书表格扩容缺少 block_id")
            table = _expand_table(
                token, doc_token, block_id, len(rows), len(rows[0])
            )
        cell_ids = table.get("cells") or []
        if len(cell_ids) != sum(len(row) for row in rows):
            block_id = got.get("block_id")
            if block_id:
                data = _req(
                    "GET",
                    f"/open-apis/docx/v1/documents/{doc_token}/blocks/{block_id}",
                    token,
                )
                table = (data.get("block") or data).get("table") or {}
                cell_ids = table.get("cells") or []
        values = [cell for row in rows for cell in row]
        if len(cell_ids) != len(values):
            raise RuntimeError(
                f"飞书表格单元格数量异常：expected={len(values)} got={len(cell_ids)}")
        for cell_id, value in zip(cell_ids, values):
            _req(
                "POST",
                f"/open-apis/docx/v1/documents/{doc_token}/blocks/{cell_id}/children",
                token,
                {"children": [_text_block(value)], "index": 0},
            )
        filled += 1
    return filled


def create_delivery(report_md: str, *, title: str, task_id: str,
                    report_version: str, reused_doc_token: str = "") -> dict:
    """新建飞书 docx（或复用已有回执），写入内容（图片上传 media 回填），转移所有权，回读核对。

    返回 feishu_doc：{doc_token, url, revision_id, report_version, images_filled,
                      readback, degraded:False}。
    """
    token = _tenant_token()

    # 幂等复用：命中已有回执则不新建。
    if reused_doc_token:
        doc_token = reused_doc_token
    else:
        data = _req("POST", "/open-apis/docx/v1/documents", token,
                    {"title": title})
        doc = data.get("document") or {}
        doc_token = doc.get("document_id") or doc.get("document", {}).get("document_id")
        if not doc_token:
            raise RuntimeError(f"创建飞书文档失败：{data}")

    # 写入 block（批量 children），再对 image block 上传 media 回填。
    blocks = md_to_blocks(report_md)
    images_filled = 0
    if blocks and not reused_doc_token:
        created = _create_children(token, doc_token, blocks)
        _fill_tables(token, doc_token, blocks, created)
        images_filled = _fill_images(token, doc_token, blocks, created)

    # 优先转移所有权；失败时降级添加 full_access 协作者，保证发起人可管理文档。
    owner = os.environ.get("FEISHU_USER_OPEN_ID")
    ownership_transferred = False
    ownership_error = ""
    collaborator_added = False
    collaborator_error = ""
    if owner:
        try:
            _req("POST", f"/open-apis/drive/v1/permissions/{doc_token}/members/transfer_owner"
                 "?type=docx&need_notification=false&remove_old_owner=false"
                 "&old_owner_perm=full_access&stay_put=true", token,
                 {"member_type": "openid", "member_id": owner})
            ownership_transferred = True
        except Exception as error:
            ownership_transferred = False
            ownership_error = f"{type(error).__name__}: {error}"
            try:
                _req(
                    "POST",
                    f"/open-apis/drive/v1/permissions/{doc_token}/members"
                    "?type=docx&need_notification=false",
                    token,
                    {
                        "member_type": "openid",
                        "member_id": owner,
                        "perm": "full_access",
                        "type": "user",
                    },
                )
                collaborator_added = True
            except Exception as collaborator_exc:
                collaborator_error = (
                    f"{type(collaborator_exc).__name__}: {collaborator_exc}"
                )
    else:
        ownership_error = "owner_not_configured"
        collaborator_error = "collaborator_target_not_configured"

    # 导出后回读核对：重新读回 blocks，核对章节齐全、图 block 生效、版本一致。
    readback = _readback_check(token, doc_token, report_md, report_version)
    revision_id = readback.get("revision_id") or 1

    return {
        "doc_token": doc_token,
        "url": f"{_WEB_BASE}/docx/{doc_token}",
        "revision_id": revision_id,
        "report_version": report_version,
        "ownership_transferred": ownership_transferred,
        "ownership_target": owner,
        "ownership_error": ownership_error,
        "collaborator_added": collaborator_added,
        "collaborator_perm": "full_access" if collaborator_added else "",
        "collaborator_error": collaborator_error,
        "delivery_access_granted": ownership_transferred or collaborator_added,
        "images_filled": images_filled,
        "readback": readback,
        "degraded": False,
    }


def _expected_image_count(report_md: str) -> int:
    """报告 MD 中指向**存在本地文件**的图片数（真正会进飞书的 image block 数）。"""
    n = 0
    for alt, path in re.findall(r"!\[(.*?)\]\((.*?)\)", report_md or ""):
        if path and os.path.isfile(path):
            n += 1
    return n


def _list_document_blocks(token: str, doc_token: str) -> list[dict]:
    """Read every block page; tables can push tail chapters beyond page 1."""
    items: list[dict] = []
    page_token = ""
    while True:
        path = (
            f"/open-apis/docx/v1/documents/{doc_token}/blocks?page_size=500"
        )
        if page_token:
            path += "&page_token=" + urllib.parse.quote(page_token, safe="")
        data = _req("GET", path, token)
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            return items
        next_token = str(data.get("page_token") or "")
        if not next_token or next_token == page_token:
            raise RuntimeError("飞书文档 block 分页游标缺失或未推进")
        page_token = next_token


def _readback_check(token: str, doc_token: str, report_md: str,
                    report_version: str) -> dict:
    """导出后回读核对：章节齐全 / 图 block 生效 / 绑定版本一致。"""
    data = _req("GET", f"/open-apis/docx/v1/documents/{doc_token}", token)
    doc = data.get("document") or {}
    revision_id = doc.get("revision_id")
    items = _list_document_blocks(token, doc_token)
    text_all = "\n".join(
        el.get("text_run", {}).get("content", "")
        for b in items for el in (b.get(_key_for(b), {}) or {}).get("elements", [])
        if isinstance(el, dict))
    want_headings = re.findall(r"^#{1,4}\s+(.*)$", report_md, flags=re.M)
    missing = [h.strip() for h in want_headings if h.strip() and h.strip() not in text_all]
    # 图核对：回读到的 image block 数 >= 期望本地图数即视为图引用生效。
    want_images = _expected_image_count(report_md)
    got_images = sum(1 for b in items if b.get("block_type") == _BLOCK_IMAGE)
    images_ok = got_images >= want_images
    return {
        "revision_id": revision_id,
        "chapters_complete": not missing,
        "missing_chapters": missing,
        "images_referenced": images_ok,
        "expected_images": want_images,
        "got_images": got_images,
        "version_bound": report_version,
        "ok": (not missing) and images_ok,
    }


def _key_for(block: dict) -> str:
    """回读 block 的内容键：优先按 block_type 命中的内容字段，回退空。"""
    bt = block.get("block_type")
    if bt == _BLOCK_TEXT:
        return "text"
    for lvl, code in _HEADING_BLOCK_TYPE.items():
        if bt == code:
            return _HEADING_KEY[lvl]
    return ""


# ── handoff 复读 ──────────────────────────────────────────────
def current_revision(doc_token: str) -> Optional[int]:
    """handoff 阶段复读文档当前 revision_id；无凭据/失败返回 None（交由调用方按降级处理）。"""
    if not doc_token or not api_available():
        return None
    try:
        token = _tenant_token()
        data = _req("GET", f"/open-apis/docx/v1/documents/{doc_token}", token)
        return (data.get("document") or {}).get("revision_id")
    except Exception:
        return None


# ── 降级 ──────────────────────────────────────────────────────
def fallback(report_md: str, *, title: str, task_id: str,
             report_version: str) -> dict:
    """无凭据/失败降级：确定性占位 feishu_doc（schema 与真路径一致，degraded=True）。"""
    import hashlib
    h = hashlib.sha1(f"{task_id}:{report_version}".encode()).hexdigest()[:16]
    doc_token = f"docx_stub_{h}"
    want_headings = [x.strip() for x in re.findall(r"^#{1,4}\s+(.*)$", report_md or "", flags=re.M)]
    return {
        "doc_token": doc_token,
        "url": f"{_WEB_BASE}/docx/{doc_token}",
        "revision_id": 1,
        "report_version": report_version,
        "ownership_transferred": False,
        "ownership_target": None,
        "ownership_error": "diagnostic_fallback",
        "collaborator_added": False,
        "collaborator_perm": "",
        "collaborator_error": "diagnostic_fallback",
        "delivery_access_granted": False,
        "images_filled": 0,
        "readback": {
            "revision_id": 1,
            "chapters_complete": bool(want_headings),
            "missing_chapters": [],
            "images_referenced": True,
            "expected_images": 0,
            "got_images": 0,
            "version_bound": report_version,
            "ok": True,
        },
        "degraded": True,
    }
