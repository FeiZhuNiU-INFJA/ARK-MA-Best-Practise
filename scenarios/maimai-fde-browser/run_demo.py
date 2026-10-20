"""run_demo.py —— 脉脉 FDE 职位抓取 demo 的 MA 编排入口。

一条命令跑通整条链路：
  上传沙箱脚本 → 建 Agent → 建 Environment（装 Playwright+Chromium）→ 建 Session（挂脚本）
  → 让 Agent 后台拉起脚本 → **编排侧直接轮询 Files API** 取 state.json/二维码/结果
  → 二维码就绪时存到本地并提示你在 MA 客户端扫码
  → 出现扫码之外的额外验证时**暂停并通知你**，等你回车继续
  → 登录成功后抓取前 10 个 FDE 职位，产物下载到本地 out/。

为什么编排侧直接轮询 Files API（而不是只靠 Agent 对话回报）：
  沙箱里的浏览器是后台常驻进程，它把状态写进 /mnt/session/outputs/（自动注册到 Files API）。
  编排侧按固定节奏 `GET /files?scope_id=<session>&purpose=agent` 下载 state.json，就能与 Agent
  的对话轮次解耦地、稳定地拿到最新相位——这是「长任务 + 人工介入」最可靠的驱动方式。

责任边界（谁做什么）见本目录 README 的流程表。

用法：
  python3 run_demo.py            # 跑完整链路（需要你用手机扫码）
  python3 run_demo.py --keep     # 结束后保留 agent/env/session，便于复查
  python3 run_demo.py --no-launch-wait  # 不等 Agent 拉起轮次，直接进轮询（调试用）

配置来源（按优先级）：环境变量 > ./config.env（KEY=value 行）。必需 ARK_API_KEY。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ark_client import ArkClient, ArkError
from agent_config import ENV_PACKAGES, ENV_SETUP_SCRIPT, MODEL_ID, build_agent_config

HERE = Path(__file__).resolve().parent
SANDBOX_SCRIPT = HERE / "sandbox" / "maimai_login_search.py"
OUT_DIR = HERE / "out"
MOUNT_PATH = "maimai_login_search.py"  # 相对 /mnt/session/uploads/
SANDBOX_MOUNT_FULL = f"/mnt/session/uploads/{MOUNT_PATH}"
# 自愈选择器库：随 session 一起挂到 /mnt/session/uploads/，沙箱脚本启动时加载它覆盖内置默认；
# done 时把沙箱写出的 discovered.json 合并回这个本机文件，实现「学一次、下次先用」。
LEARNED_PATH = HERE / "learned_selectors.json"
LEARNED_MOUNT = "learned_selectors.json"

LAUNCH_MESSAGE = (
    "请开始任务：先 `ls -la /mnt/session/uploads/` 确认 maimai_login_search.py 在，"
    f"然后用这条命令在后台拉起它：\n"
    f"nohup python3 {SANDBOX_MOUNT_FULL} > /mnt/session/outputs/sandbox.log 2>&1 &\n"
    "拉起后立刻结束本轮，回复「已在后台拉起脉脉登录脚本」。不要前台等待它结束。"
)

# 轮询期间的会话心跳：扫码等待期没有任何对话事件，MA 会判定 session 空闲并回收它，
# 导致产物被清、state.json 停更。周期性发这条无害消息制造活动，给 session 续命。
# 刻意要求模型只回两个字、不跑任何命令，避免心跳触发额外 bash 动作。
KEEPALIVE_MESSAGE = (
    "（系统心跳，请忽略本条内容）登录脚本仍在后台等待扫码，"
    "请只回复「收到」两个字，不要运行任何 bash 命令、不要做任何其它操作。"
)

# 终态/轮次结束事件（对齐方舟 SSE 约定）。
TURN_END = {"session.status_idle", "session.status_terminated"}


def load_config() -> dict:
    """环境变量优先；再读同目录 config.env（支持 KEY=value 与 KEY="json"）。"""
    cfg: dict[str, str] = {}
    env_file = HERE / "config.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                try:
                    val = json.loads(val) if val[0] == '"' else val[1:-1]
                except json.JSONDecodeError:
                    val = val[1:-1]
            cfg[key.strip()] = val
    for k in ("ARK_API_KEY", "ARK_BASE_URL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    if not cfg.get("ARK_API_KEY"):
        raise SystemExit("缺少 ARK_API_KEY：请设环境变量或在 config.env 里配置（见 env.example）。")
    return cfg


def banner(title: str, body: str = "") -> None:
    line = "=" * 64
    print(f"\n{line}\n{title}\n{line}")
    if body:
        print(body)


def try_open(path: Path) -> None:
    """尽量在本机把图片打开给你看（macOS: open / Linux: xdg-open）。失败就只打印路径。"""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


async def consume_launch_turn(ark: ArkClient, session_id: str, timeout_s: float = 180) -> None:
    """消费 Agent 拉起脚本的那一轮，直到 session.status_idle/end_turn 或超时。"""
    deadline = time.time() + timeout_s
    async for ev in ark.stream_events(session_id):
        et = ev.get("type", "")
        if et == "agent.message":
            txt = "".join(b.get("text", "") for b in (ev.get("content") or [])
                          if isinstance(b, dict) and b.get("type") == "text")
            if txt.strip():
                print(f"[agent] {txt.strip()[:200]}")
        elif et in TURN_END:
            sr = ev.get("stop_reason") or {}
            sr_type = sr.get("type") if isinstance(sr, dict) else sr
            if et == "session.status_idle" and sr_type == "requires_action":
                continue
            print(f"[launch] 轮次结束 stop_reason={sr_type}")
            return
        elif et == "session.error":
            print(f"[launch] session.error: {json.dumps(ev, ensure_ascii=False)[:200]}")
            return
        if time.time() > deadline:
            print("[launch] 等待拉起轮次超时，继续进入轮询。")
            return


async def fetch_outputs(ark: ArkClient, session_id: str) -> dict[str, dict]:
    """列出当前 Session 产物，返回 {filename: file_meta}。"""
    files = await ark.list_session_outputs(session_id)
    return {f["filename"]: f for f in files if f.get("filename")}


async def download_named(ark: ArkClient, outputs: dict[str, dict], name: str,
                         cache: dict[str, tuple] | None = None) -> Path | None:
    """下载 outputs 里名为 name 的产物到 OUT_DIR。

    cache（可选）：{filename: (file_id, bytes)} 的下载去重表。每个预签名 download_url 在 TOS
    有访问配额，state.json 每 4s 都重下会触发 UrlQuotaExceed。传了 cache 时，若该文件的
    (id, bytes) 与上次相同且本地已存在，就跳过下载、直接返回本地路径，避免重复打同一个 URL。
    """
    meta = outputs.get(name)
    if not meta or not meta.get("download_url"):
        return None
    dest = OUT_DIR / name
    if cache is not None:
        sig = (meta.get("id"), meta.get("bytes"))
        if cache.get(name) == sig and dest.exists():
            return dest
    try:
        await ark.download(meta["download_url"], dest)
        if cache is not None:
            cache[name] = (meta.get("id"), meta.get("bytes"))
        return dest
    except ArkError as e:
        print(f"[warn] 下载 {name} 失败：{e}")
        return None


async def poll_loop(ark: ArkClient, session_id: str, *, overall_timeout_s: float = 600,
                    interval_s: float = 4, keepalive_s: float = 90) -> dict:
    """核心轮询：从 Files API 取 state.json，按 phase 驱动人工介入与收尾。返回最终 state。

    keepalive_s：扫码等待期每隔这么多秒给 session 发一条心跳消息。没有任何对话事件时
    MA 会判定 session 空闲并回收（产物被清、state.json 停更），心跳制造活动给它续命。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + overall_timeout_s
    last_phase = None
    last_qr_version = None
    verify_paused = False
    final_state: dict = {}
    dl_cache: dict[str, tuple] = {}  # 产物下载去重表，避免重复打同一预签名 URL 触发 UrlQuotaExceed
    next_keepalive = time.time() + keepalive_s

    while time.time() < deadline:
        # 全程周期性发心跳，防止 session 因空闲被回收。关键：登录成功后进入「招聘管理→搜人才→抓取」
        # 阶段仍可能耗时较久，若此时停发心跳，MA 会判定 session 空闲把沙箱回收、产物永久冻结
        # （曾实测登录后停更）。故 done 之前的所有相位都持续发心跳，直到拿到最终结果。
        if time.time() >= next_keepalive and last_phase not in ("done", "error"):
            try:
                await ark.send_user_message(session_id, KEEPALIVE_MESSAGE)
                print(f"[keepalive] 已发送会话心跳（防 session 空闲回收）")
            except ArkError as e:
                print(f"[keepalive] 心跳发送失败（忽略）：{e}")
            next_keepalive = time.time() + keepalive_s

        # Files API 偶发瞬时网络抖动（httpx.ReadError/超时等）。单次失败不该让整个扫码会话崩掉，
        # 否则用户手里的二维码白白过期。这里兜住任何异常：本 tick 跳过，下个 tick 再试。
        try:
            outputs = await fetch_outputs(ark, session_id)
            state_path = await download_named(ark, outputs, "state.json", dl_cache)
        except Exception as e:
            print(f"[poll] 拉取产物瞬时失败（忽略，稍后重试）：{type(e).__name__}: {e}")
            await asyncio.sleep(interval_s)
            continue
        if state_path is None:
            # 脚本可能还在装机/启动；稍等再看。
            await asyncio.sleep(interval_s)
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            await asyncio.sleep(interval_s)
            continue
        final_state = state
        phase = state.get("phase")
        if phase != last_phase:
            print(f"[state] phase={phase}  {json.dumps({k: v for k, v in state.items() if k not in ('phase',)}, ensure_ascii=False)[:200]}")
            last_phase = phase

        if phase == "waiting_qr_scan":
            # 二维码每隔约 100s 会被沙箱重载刷新（qr_version +1）。版本变了就重新拉取 + 打开，
            # 保证你手上的二维码始终是最新有效的那张，不会扫到过期码。
            qr_version = state.get("qr_version", 1)
            if qr_version != last_qr_version:
                qr = await download_named(ark, outputs, "qrcode.png", dl_cache)
                if qr:
                    first = last_qr_version is None
                    last_qr_version = qr_version
                    title = ("📱 二维码已就绪 —— 请在 MA 客户端用脉脉 App 扫码登录" if first
                             else f"🔄 二维码已刷新（第 {qr_version} 张）—— 请扫最新的这张")
                    banner(title,
                           f"二维码已下载到：{qr}\n（已尝试自动打开；若没弹出，请手动打开该图片扫码）\n"
                           f"提示：{state.get('hint', '')}\n"
                           "登录成功后脚本会自动继续搜索 FDE 职位。")
                    try_open(qr)

        elif phase == "need_manual_verify" and not verify_paused:
            verify_paused = True
            vf = await download_named(ark, outputs, "verify.png", dl_cache)
            banner("⏸️  出现扫码之外的额外验证 —— 已暂停，等你处理",
                   f"现场截图：{vf or '(未取到 verify.png)'}\n"
                   f"提示：{state.get('hint', '')}\n"
                   "脚本已暂停等待你人工处理（短信/滑块/人脸等）。\n"
                   "处理完成后，脚本检测到登录成功会自动继续。")
            # 把暂停通知也送进 MA 客户端对话，让 Agent 对话侧也能看到。
            try:
                await ark.send_user_message(
                    session_id,
                    "沙箱里的登录出现了扫码之外的额外验证，脚本已暂停。请读 "
                    "`cat /mnt/session/outputs/state.json` 并如实告诉我当前验证类型，等我进一步指示。")
            except ArkError:
                pass

        elif phase == "done":
            await download_named(ark, outputs, "result.json", dl_cache)
            await download_named(ark, outputs, "result.md", dl_cache)
            await download_named(ark, outputs, "sandbox.log", dl_cache)
            # 登录后的职位页 DOM/截图/纯文本：降级时 job_page.txt 用来喂模型兜底解析，故一并落到本地。
            await download_named(ark, outputs, "job_page.html", dl_cache)
            await download_named(ark, outputs, "job_page.png", dl_cache)
            await download_named(ark, outputs, "job_page.txt", dl_cache)
            await download_named(ark, outputs, "post_login.txt", dl_cache)
            # 自愈学习原料：登录后页面完整 DOM、全部导航 anchor、本轮运行时学到的 href/选择器。
            await download_named(ark, outputs, "post_login.html", dl_cache)
            await download_named(ark, outputs, "nav_anchors.json", dl_cache)
            await download_named(ark, outputs, "discovered.json", dl_cache)
            return final_state

        elif phase == "error":
            await download_named(ark, outputs, "sandbox.log", dl_cache)
            # error 之后脚本还会尝试降级输出 done；再等一会儿看是否转 done。
            await asyncio.sleep(interval_s)
            continue

        await asyncio.sleep(interval_s)

    print("[poll] 整体超时，返回最后一次已知状态。")
    return final_state


EXTRACT_SYSTEM = (
    "你是一个网页信息抽取器。用户会给你一段「脉脉招聘管理·人才搜索结果页」的纯文本（body innerText）。"
    "请从中抽取**人才**的概括性信息，**只输出一个 JSON 数组**，不要任何解释、不要 markdown 代码块围栏。"
    "数组每个元素形如："
    '{"name": 姓名或"", "headline": 职位/头衔概括, "company": 公司或"", "experience": 经验概括或"", '
    '"location": 地点或"", "summary": 对该人才的概括性描述, "tags": [标签], "url": 链接或""}。'
    "规则：① 非会员通常只能看到约 3 条、且姓名/公司常被隐藏，看不到就填空字符串 \"\"；"
    "② 最多输出前 10 条，按页面出现顺序；③ tags 取不到填 []；"
    "④ 只抽取真实出现在文本里的人才信息，**绝对不要编造**；⑤ 如果这段文本里根本没有人才信息"
    "（例如是登录页、空结果页、报错页），就输出空数组 []。"
)


def _parse_talents_json(text: str) -> list[dict]:
    """从模型输出里解析出人才 JSON 数组。容忍 ```json 围栏与数组前后的杂字。"""
    s = (text or "").strip()
    if s.startswith("```"):  # 去掉 ```json ... ``` 围栏
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    # 直接解析；失败则截取第一个 [ 到最后一个 ] 之间的内容再试。
    for candidate in (s, s[s.find("["): s.rfind("]") + 1] if "[" in s and "]" in s else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [j for j in data if isinstance(j, dict)]
        except json.JSONDecodeError:
            continue
    return []


def _write_results(talents: list[dict], keyword: str, *, degraded: bool, reason: str,
                   source: str) -> None:
    """用抽取到的人才覆盖写 out/result.json 与 out/result.md（与沙箱 save_results 的形状一致）。"""
    norm: list[dict] = []
    for t in talents:
        norm.append({
            "name": str(t.get("name", "") or ""),
            "headline": str(t.get("headline", "") or ""),
            "company": str(t.get("company", "") or ""),
            "experience": str(t.get("experience", "") or ""),
            "location": str(t.get("location", "") or ""),
            "summary": str(t.get("summary", "") or ""),
            "tags": [str(x) for x in (t.get("tags") or []) if str(x).strip()],
            "url": str(t.get("url", "") or ""),
            "source": source,
        })
    (OUT_DIR / "result.json").write_text(json.dumps({
        "keyword": keyword,
        "count": len(norm),
        "degraded": degraded,
        "reason": reason,
        "source": source,
        "talents": norm,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 脉脉「{keyword}」人才 Top {len(norm)}（招聘管理·非会员可见的概括信息）", ""]
    if source == "llm_extracted":
        lines += ["> 由模型从登录后真实人才页文本中抽取（选择器未命中，改用 LLM 解析）。", ""]
    for i, t in enumerate(norm, 1):
        tags = " / ".join(t.get("tags") or [])
        lines.append(f"## {i}. {t.get('headline') or '(无标题)'}")
        if t.get("name"):
            lines.append(f"- 姓名：{t.get('name')}")
        lines.append(f"- 公司：{t.get('company') or '-'}")
        lines.append(f"- 经验：{t.get('experience') or '-'}")
        lines.append(f"- 地点：{t.get('location') or '-'}")
        if t.get("summary"):
            lines.append(f"- 概括：{t.get('summary')}")
        if tags:
            lines.append(f"- 标签：{tags}")
        lines.append("")
    (OUT_DIR / "result.md").write_text("\n".join(lines), encoding="utf-8")


async def llm_extract_talents(ark: ArkClient, state: dict) -> bool:
    """降级兜底：把登录后人才页纯文本喂给 doubao 抽取人才概括信息，成功则覆盖写本地 result.*。

    仅在 done 且 degraded（选择器没抓到）且本地有 job_page.txt 时调用。
    - 模型抽到人才 → 覆盖 result.json/md，degraded=false、source=llm_extracted，返回 True。
    - 模型判定页面无人才（返回空数组）→ 保留沙箱的样例产物，返回 False（如实说明）。
    这样就不用把脉脉 DOM 选择器写死：页面结构日后变了，靠模型读真实文本依然能抽出来。
    """
    txt_path = OUT_DIR / "job_page.txt"
    if not txt_path.exists():
        print("[llm] 没有 job_page.txt，无法用模型解析（可能登录后未成功 dump 页面）。")
        return False
    page_text = txt_path.read_text(encoding="utf-8").strip()
    if not page_text:
        print("[llm] job_page.txt 为空，跳过模型解析。")
        return False
    keyword = state.get("keyword") or "FDE"
    # 控制输入规模，避免超长：取前 ~20000 字符足够覆盖首屏人才列表。
    snippet = page_text[:20000]
    banner("🤖 选择器未命中 —— 改用模型从真实人才页文本中抽取人才概括信息",
           f"页面文本长度 {len(page_text)} 字符，送入模型 {len(snippet)} 字符。")
    try:
        out = await ark.chat_completions(MODEL_ID, [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content":
                f"搜索关键词：{keyword}\n以下是脉脉招聘管理人才搜索结果页的纯文本，请抽取人才概括信息：\n\n{snippet}"},
        ])
    except ArkError as e:
        print(f"[llm] 模型解析失败：{e}")
        return False
    talents = _parse_talents_json(out)
    if not talents:
        print("[llm] 模型判定该页面无可抽取的人才（或解析为空），保留样例产物并如实说明。")
        return False
    talents = talents[:10]
    _write_results(talents, keyword, degraded=False,
                   reason="选择器未命中，由模型从真实人才页文本抽取", source="llm_extracted")
    print(f"[llm] 模型抽取成功：{len(talents)} 条人才，已覆盖写 out/result.json / result.md。")
    return True


def merge_discovered_into_learned() -> None:
    """把本轮沙箱写出的 out/discovered.json 合并回本机 learned_selectors.json（学习回填）。

    这是自愈闭环的「固化」一步：沙箱运行时真实学到的招聘入口 href、搜索框/卡片选择器，
    写进学习库，下次先用学到的、失败再降级重学。只覆盖确实学到的字段，不动其它。
    """
    disc_path = OUT_DIR / "discovered.json"
    if not disc_path.exists():
        return
    try:
        disc = json.loads(disc_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[learn] 读取 discovered.json 失败（跳过回填）：{e}")
        return
    if not isinstance(disc, dict) or not disc:
        return
    try:
        learned = json.loads(LEARNED_PATH.read_text(encoding="utf-8")) if LEARNED_PATH.exists() else {}
    except Exception:
        learned = {}

    changed = []
    # 招聘入口真实 href → recruit_nav.resolved_href（下次 open_recruit_page 固化优先用它）。
    href = disc.get("recruit_href")
    if href:
        learned.setdefault("recruit_nav", {})
        if learned["recruit_nav"].get("resolved_href") != href:
            learned["recruit_nav"]["resolved_href"] = href
            changed.append(f"recruit_nav.resolved_href={href}")
    # 招聘 anchor 选择器 → 提到 anchor_selectors 最前面（命中过的优先）。
    anchor_sel = disc.get("recruit_anchor_selector")
    if anchor_sel:
        learned.setdefault("recruit_nav", {})
        lst = learned["recruit_nav"].get("anchor_selectors") or []
        if anchor_sel in lst:
            lst.remove(anchor_sel)
        lst.insert(0, anchor_sel)
        learned["recruit_nav"]["anchor_selectors"] = lst
        changed.append(f"recruit_nav.anchor_selectors↑{anchor_sel}")
    # 命中的搜索框 / 人才卡片选择器 → 提到各自列表最前。
    for disc_key, grp, field in (
        ("search_box_selector", "search_box", "selectors"),
        ("talent_card_selector", "talent_card", "selectors"),
    ):
        sel = disc.get(disc_key)
        if not sel:
            continue
        learned.setdefault(grp, {})
        lst = learned[grp].get(field) or []
        if sel in lst:
            lst.remove(sel)
        lst.insert(0, sel)
        learned[grp][field] = lst
        changed.append(f"{grp}.{field}↑{sel}")

    if not changed:
        return
    learned["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        LEARNED_PATH.write_text(json.dumps(learned, ensure_ascii=False, indent=2), encoding="utf-8")
        banner("🧠 已把本轮学到的选择器固化回学习库", "\n".join(f"  • {c}" for c in changed) +
               f"\n写入：{LEARNED_PATH}")
    except Exception as e:
        print(f"[learn] 写回 learned_selectors.json 失败：{e}")


INFER_SELECTORS_SYSTEM = (
    "你是一个前端 DOM 分析器。用户给你一段脉脉网页的 HTML，请据此推断 CSS 选择器。"
    "**只输出一个 JSON 对象**，不要解释、不要 markdown 围栏。对象形如："
    '{"recruit_nav_anchor": "进入『招聘管理』/招聘/找人才页的 <a> 的 CSS 选择器或 \"\"", '
    '"recruit_href": "该 <a> 的 href 原值或 \"\"", '
    '"search_box": "人才搜索输入框的 CSS 选择器或 \"\"", '
    '"talent_card": "单个人才卡片的 CSS 选择器或 \"\""}。'
    "规则：① 选择器要尽量稳定（优先用 href 片段、role、可见文本类名）；② 拿不到的字段填空字符串；"
    "③ 只根据 HTML 里真实出现的元素推断，不要编造类名。"
)


async def llm_infer_selectors(ark: ArkClient) -> bool:
    """降级兜底·学习增强：把登录后社区页 HTML 喂给模型推断招聘管理入口/搜索框/人才卡片选择器，合并回学习库。

    仅在降级且本地有 post_login.html 时调用。推断到的选择器写进 discovered.json 再合并回
    learned_selectors.json——下次沙箱就能先用模型学到的选择器，真正形成「越跑越准」的闭环。
    """
    html_path = OUT_DIR / "post_login.html"
    if not html_path.exists():
        return False
    html = html_path.read_text(encoding="utf-8").strip()
    if not html:
        return False
    snippet = html[:30000]  # 控制输入规模；首屏导航/卡片结构通常在前段。
    banner("🤖 让模型从登录后真实 DOM 推断招聘管理入口/搜索框/人才卡片选择器", f"送入 {len(snippet)} 字符 HTML。")
    try:
        out = await ark.chat_completions(MODEL_ID, [
            {"role": "system", "content": INFER_SELECTORS_SYSTEM},
            {"role": "user", "content": f"以下是脉脉登录后页面的 HTML，请推断选择器：\n\n{snippet}"},
        ])
    except ArkError as e:
        print(f"[llm] 选择器推断失败：{e}")
        return False
    s = (out or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    try:
        obj = json.loads(s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else s)
    except Exception:
        print("[llm] 选择器推断结果无法解析为 JSON，跳过。")
        return False
    if not isinstance(obj, dict):
        return False
    # 把模型推断塞进 discovered.json（与沙箱运行时学到的同格式），再统一合并回学习库。
    disc_path = OUT_DIR / "discovered.json"
    try:
        disc = json.loads(disc_path.read_text(encoding="utf-8")) if disc_path.exists() else {}
    except Exception:
        disc = {}
    mapping = {
        "recruit_href": obj.get("recruit_href"),
        "recruit_anchor_selector": obj.get("recruit_nav_anchor"),
        "search_box_selector": obj.get("search_box"),
        "talent_card_selector": obj.get("talent_card"),
    }
    added = {k: v for k, v in mapping.items() if v and not disc.get(k)}
    if not added:
        print("[llm] 模型未给出新的可用选择器（或已有运行时学到的，优先保留）。")
        return False
    disc.update(added)
    disc["_llm_inferred"] = True
    try:
        disc_path.write_text(json.dumps(disc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[llm] 写 discovered.json 失败：{e}")
        return False
    print(f"[llm] 模型推断出选择器：{json.dumps(added, ensure_ascii=False)}")
    return True


def print_result(state: dict) -> None:
    result = OUT_DIR / "result.json"
    if not result.exists():
        banner("结果", f"最终 phase={state.get('phase')}；未取到 result.json。请查看 out/sandbox.log 排障。")
        return
    data = json.loads(result.read_text(encoding="utf-8"))
    talents = data.get("talents", [])
    degraded = data.get("degraded")
    head = f"共 {len(talents)} 条「{data.get('keyword')}」人才（招聘管理·概括信息）"
    if degraded:
        head += f"  ⚠️ 降级样例（真实抓取失败：{data.get('reason')}）"
    banner("✅ 完成 —— " + head, f"产物目录：{OUT_DIR}")
    for i, t in enumerate(talents, 1):
        print(f"{i:>2}. {t.get('headline','')} | {t.get('company','') or '-'} | "
              f"{t.get('experience','') or '-'} | {t.get('location','') or '-'}")
        if t.get("summary"):
            print(f"    {t['summary']}")


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    base_url = cfg.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
    suffix = str(int(time.time()))

    async with ArkClient(cfg["ARK_API_KEY"], base_url) as ark:
        agent_id = env_id = session_id = ""
        try:
            print("[1/5] 上传沙箱脚本到 Files API ...")
            file_id = await ark.upload_file(SANDBOX_SCRIPT, purpose="agent")
            print(f"      file_id={file_id}")
            # 自愈选择器库也上传挂载（有就传；首轮没学到东西也无妨，沙箱会用内置默认）。
            learned_file_id = ""
            if LEARNED_PATH.exists():
                learned_file_id = await ark.upload_file(LEARNED_PATH, purpose="agent")
                print(f"      learned_file_id={learned_file_id}")

            print("[2/5] 创建 Agent ...")
            agent_id = await ark.create_agent(build_agent_config())
            print(f"      agent={agent_id}")

            print("[3/5] 创建 Environment（装 Playwright + Chromium，首次启动较慢）...")
            env_id = await ark.create_environment(
                f"maimai-fde-browser-{suffix}",
                packages=ENV_PACKAGES, setup_script=ENV_SETUP_SCRIPT)
            print(f"      env={env_id}")

            print("[4/5] 创建 Session 并挂载脚本到 /mnt/session/uploads/ ...")
            resources = [{"type": "file", "file_id": file_id, "mount_path": MOUNT_PATH}]
            if learned_file_id:
                resources.append({"type": "file", "file_id": learned_file_id,
                                  "mount_path": LEARNED_MOUNT})
            session_id = await ark.create_session(
                agent_id, env_id, title="脉脉 FDE 职位抓取",
                resources=resources)
            print(f"      session={session_id}")

            print("[5/5] 让 Agent 在后台拉起登录脚本 ...")
            await ark.send_user_message(session_id, LAUNCH_MESSAGE)
            if not args.no_launch_wait:
                await consume_launch_turn(ark, session_id)

            banner("开始轮询沙箱状态（扫码 → 搜索 → 抓取）",
                   "接下来二维码就绪时会提示你扫码；若出现额外验证会暂停并通知你。")
            state = await poll_loop(ark, session_id,
                                    overall_timeout_s=args.timeout,
                                    interval_s=args.interval,
                                    keepalive_s=args.keepalive)
            # 选择器没抓到（degraded）时，改用模型读登录后人才页真实文本抽取——不把 DOM 写死。
            if state.get("phase") == "done" and state.get("degraded"):
                await llm_extract_talents(ark, state)
                # 学习增强：让模型从登录后真实 DOM 推断招聘管理入口/搜索框/人才卡片选择器，塞进 discovered.json。
                await llm_infer_selectors(ark)
            # 学习回填：把本轮运行时学到的 + 模型推断的选择器合并回本机 learned_selectors.json。
            merge_discovered_into_learned()
            print_result(state)
            return 0
        except ArkError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        finally:
            if not args.keep:
                print("\n[cleanup] 删除 session/env/agent（--keep 可保留）...")
                if session_id:
                    await ark.delete_session(session_id)
                if env_id:
                    await ark.delete_environment(env_id)
                if agent_id:
                    await ark.delete_agent(agent_id)
            else:
                print(f"\n[keep] 保留资源：agent={agent_id} env={env_id} session={session_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description="脉脉 FDE 职位抓取 demo（方舟 MA + headless 浏览器）")
    ap.add_argument("--keep", action="store_true", help="结束后保留 agent/env/session")
    ap.add_argument("--no-launch-wait", action="store_true", help="不等 Agent 拉起轮次，直接进轮询")
    ap.add_argument("--timeout", type=float, default=600, help="轮询整体超时（秒），默认 600")
    ap.add_argument("--interval", type=float, default=4, help="轮询间隔（秒），默认 4")
    ap.add_argument("--keepalive", type=float, default=90,
                    help="扫码等待期发会话心跳的间隔（秒），防 session 空闲被回收，默认 90")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
