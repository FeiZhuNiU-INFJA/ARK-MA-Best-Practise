"""maimai_login_search.py —— 沙箱内后台常驻的脉脉（maimai.cn）「扫码登录 + 搜 FDE 职位」脚本。

本脚本运行在火山方舟 MA 云端沙箱里（Linux，无 GUI），由 Agent 用一条后台命令拉起：

    nohup python3 /mnt/session/uploads/maimai_login_search.py \
        > /mnt/session/outputs/sandbox.log 2>&1 &

为什么必须「后台常驻单进程」（核心约束）：
  * 扫码登录的二维码会话与登录态绑定在**同一个浏览器进程**里。二维码从展示到被手机扫描、
    再到服务端把该浏览器会话标记为已登录，整个过程必须由同一个常驻的 Chromium 实例持有。
  * MA 沙箱里 Agent 的每次 bash 调用之间进程不保活，所以不能「开浏览器→退出→再开浏览器」分段做；
    必须一个长命进程从头跟到尾。Agent 侧只负责「拉起它 + 轮询它写出的状态文件」。

进程间通信全部走**文件信号**（放在 /mnt/session/outputs/，会自动注册到 Files API 供编排侧下载）：
  * state.json        —— 状态机当前相位 + 结构化上下文（唯一事实源）。
  * qrcode.png        —— 待扫描的登录二维码截图（脉脉二维码是 <canvas>，用 locator 截元素）。
  * verify.png        —— 出现「扫码之外的额外验证」（滑块/短信/人脸等）时的现场截图。
  * job_page.png      —— 登录后职位搜索页的整页截图（供精调选择器）。
  * job_page.html     —— 登录后职位搜索页的 DOM 快照（供精调选择器，职位页结构未知时尤其有用）。
  * job_page.txt      —— 登录后职位页的纯文本（body innerText）。选择器抓不到时，编排侧把这段
                        文本喂给模型抽取职位，避免把选择器写死——脉脉 DOM 日后变了也能扛。
  * result.json / result.md —— 登录成功后搜到的前 N 个 FDE 职位。
  * sandbox.log       —— 本进程 stdout/stderr（排障用）。

状态机相位（state.json 的 phase 字段）：
  starting            刚拉起，准备启动浏览器
  waiting_qr_scan     已截好二维码，等用户在 MA 客户端扫码
  need_manual_verify  出现扫码之外的额外验证，已暂停并截图，等人工处理
  logged_in           登录成功（URL 已离开 /platform/login）
  searching           正在搜索 FDE 职位
  done                已抓到并保存职位（真实或降级样例）
  error               发生不可恢复错误

脉脉登录实测事实（本机 headless 验证，供维护者参考）：
  * https://maimai.cn/login 会 302 跳到 https://maimai.cn/platform/login，headless 下稳定存活（不清场）。
  * 默认视图是「手机验证码登录」；右上角有个 40x40 的切换图标，选择器 div.p-login-switch.qrcode，
    点击后切到「扫码登录」视图。
  * 二维码是一个 180x180 的 <canvas>，**必须用 locator.screenshot() 截该元素**（不是 <img src>）。
  * 登录成功判定：扫码确认后 URL 离开 /platform/login（跳回主站）。
  * 不登录查不了职位：职位搜索页在未登录时会重定向回登录页。故必须先完成扫码登录。
  * 登录后职位搜索页的真实 DOM 结构尚未探明，故脚本登录后先把整页 DOM/截图 dump 到产物目录，
    再用一组「通用选择器」尽力抓前 N 个；抓不到就降级为可控样例（绝不冒充真实数据）。

降级策略（符合「流程骨架优先、抓不到就降级为可控演示」的既定决策）：
  任何一步（打开页面 / 截二维码 / 搜索 / 解析）失败，都不让整条链路崩死；
  在 state.json 里标记 degraded=true + 原因，并用内置的可控样例数据产出 result.*，
  保证「起浏览器→出二维码→扫码→搜索→落盘前十」这条链路始终可复现、可演示。

本脚本**不依赖主项目任何包**，只用标准库 + playwright；脉脉页面结构随时可能变，
选择器集中在 SELECTORS 常量里便于维护。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---- 可配置项（也可用环境变量覆盖，便于演示时调参）----
OUTPUT_DIR = Path(os.environ.get("MAIMAI_OUTPUT_DIR", "/mnt/session/outputs"))
SEARCH_KEYWORD = os.environ.get("MAIMAI_SEARCH_KEYWORD", "FDE")
TOP_N = int(os.environ.get("MAIMAI_TOP_N", "10"))
# 扫码 + 登录整体等待上限（秒）：留足用户在 MA 客户端收到二维码并掏手机扫的时间。
LOGIN_TIMEOUT_S = int(os.environ.get("MAIMAI_LOGIN_TIMEOUT_S", "600"))
# 轮询登录态的间隔（秒）。
POLL_INTERVAL_S = float(os.environ.get("MAIMAI_POLL_INTERVAL_S", "3"))
# 二维码主动刷新间隔（秒）：脉脉二维码约 2 分钟失效，到点就重载登录页换一张新的，
# 避免用户看到的二维码已过期扫不动。默认 100s，留足余量。
QR_REFRESH_S = float(os.environ.get("MAIMAI_QR_REFRESH_S", "100"))
# headless：沙箱无 GUI，默认 headless。保留开关，便于本地带 GUI 调试时置 false。
HEADLESS = os.environ.get("MAIMAI_HEADLESS", "true").lower() != "false"

MAIMAI_HOME = "https://maimai.cn/"
MAIMAI_LOGIN = "https://maimai.cn/login"
# 登录中转页：maimai.cn/login 会 302 到这里。URL 是否离开它，是登录成功的判定依据。
LOGIN_URL_MARK = "/platform/login"
# 职位搜索页候选 URL（按顺序尝试；都打不开职位内容就 dump 文本交模型抽取）。{kw} 处填关键词。
# 实测：脉脉主页招聘入口是 /zp。老的 search_center/search/job/jobs 路径已失效、会被导回首页，
# 故以 /zp 为主入口；并额外带几个可能的关键词直达变体兜底。登录后这些页面才有职位内容。
JOB_SEARCH_URLS = [
    os.environ.get("MAIMAI_JOB_URL", ""),  # 允许演示时用环境变量直接指定
    "https://maimai.cn/zp?query={kw}",
    "https://maimai.cn/zp/search?query={kw}",
    "https://maimai.cn/zp",
    "https://maimai.cn/search_center?type=job&query={kw}&highlight=true",
]

STATE_PATH = OUTPUT_DIR / "state.json"
QR_PATH = OUTPUT_DIR / "qrcode.png"
VERIFY_PATH = OUTPUT_DIR / "verify.png"
JOB_PAGE_PNG = OUTPUT_DIR / "job_page.png"
JOB_PAGE_HTML = OUTPUT_DIR / "job_page.html"
JOB_PAGE_TXT = OUTPUT_DIR / "job_page.txt"
RESULT_JSON = OUTPUT_DIR / "result.json"
RESULT_MD = OUTPUT_DIR / "result.md"
# 登录后社区页的 DOM 快照 + 全部导航 <a>（text+href）——供编排侧/人读取真实招聘入口。
POST_LOGIN_HTML = OUTPUT_DIR / "post_login.html"
NAV_ANCHORS_JSON = OUTPUT_DIR / "nav_anchors.json"
# 自愈选择器库：挂载在 uploads 下（run_demo 会把本机 learned_selectors.json 一起挂进来）。
# 运行时学到的真实 href/选择器写到 outputs/discovered.json，编排侧 done 时合并回本机学习库。
LEARNED_CANDIDATES = [
    Path(os.environ.get("MAIMAI_LEARNED_PATH", "")),
    Path("/mnt/session/uploads/learned_selectors.json"),
    Path(__file__).resolve().parent / "learned_selectors.json",
    Path(__file__).resolve().parent.parent / "learned_selectors.json",
]
DISCOVERED_PATH = OUTPUT_DIR / "discovered.json"
# 运行时积累的「真实学到的东西」，收尾时落盘到 discovered.json。
DISCOVERED: dict = {}
# 「落地即极速抓取」在 open_recruit_page 里抓到的人才（新标签页被秒杀前抢下的那几条）。
# scrape_talents 若发现它非空就直接用，不再走后续会被 Target closed 打断的常规抓取。
FAST_GRABBED: list[dict] = []

# 脉脉页面选择器（结构易变，集中维护；取不到就触发降级，不让链路崩）。
SELECTORS = {
    # 登录页右上角「切到扫码登录」的开关图标（实测 div.p-login-switch.qrcode）。
    "qr_switch": [
        "div.p-login-switch.qrcode",
        ".p-login-switch.qrcode",
        "div.p-login-switch",
    ],
    # 扫码视图里的二维码（脉脉用 <canvas> 渲染，必须截元素本身）。
    "qr_canvas": [
        "canvas",
        ".qrcode canvas",
        ".login-qrcode canvas",
    ],
    # 扫码之外的额外验证标志（滑块 / 短信验证码 / 人脸等）——命中即暂停通知人工。
    "manual_verify_marker": [
        "text=请完成安全验证",
        "text=拖动滑块",
        "text=拖动下方滑块",
        "text=向右滑动",
        "text=人脸",
        ".nc_scale",          # 阿里无痕验证滑块轨道
        ".nc-container",
        ".geetest_panel",
        "iframe[src*='captcha']",
    ],
    # 登录后「招聘管理」里关键词搜人才的结果卡片（结构未探明，列一组通用候选尽力命中）。
    "talent_card": [
        "div[class*='talent']",
        "div[class*='candidate']",
        "div[class*='resume']",
        "div[class*='person-card']",
        "div[class*='user-card']",
        "li[class*='talent']",
        "li[class*='card']",
        "div[class*='card']",
    ],
}

# ===== 自愈选择器库（learned_selectors.json）=====
# 设计：固化优先 → 失败降级（dump 真实 DOM）→ 学习回填（编排侧把学到的写回本机库）。
# 这里只负责「加载本机/挂载的学习库」，并暴露便捷读取函数；运行时学到的 href/选择器塞进
# 全局 DISCOVERED，收尾时落盘到 outputs/discovered.json 由编排侧合并回 learned_selectors.json。
LEARNED: dict = {}


def load_learned() -> None:
    """按候选路径加载 learned_selectors.json 到全局 LEARNED。找不到就留空字典（用内置默认）。"""
    global LEARNED
    for p in LEARNED_CANDIDATES:
        try:
            if p and str(p) and p.exists():
                LEARNED = json.loads(p.read_text(encoding="utf-8"))
                print(f"[learned] 已加载学习库: {p}", flush=True)
                return
        except Exception as e:
            print(f"[learned] 读取 {p} 失败（忽略）: {e}", flush=True)
    print("[learned] 未找到学习库，使用内置默认选择器。", flush=True)


def learned_list(*path: str) -> list[str]:
    """从 LEARNED 里按 a.b.c 取一个候选列表；取不到返回 []（不抛）。"""
    node = LEARNED
    for key in path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return [x for x in node if isinstance(x, str)] if isinstance(node, list) else []


def record_discovered(key: str, value) -> None:
    """把运行时真实学到的东西记进 DISCOVERED（收尾落盘 discovered.json 供回填）。"""
    try:
        DISCOVERED[key] = value
        print(f"[discovered] {key} = {json.dumps(value, ensure_ascii=False)[:200]}", flush=True)
    except Exception:
        pass


def flush_discovered() -> None:
    """把这一轮学到的真实选择器/href 落盘到 outputs/discovered.json（编排侧合并回学习库）。"""
    if not DISCOVERED:
        return
    try:
        DISCOVERED["updated_at"] = _now()
        DISCOVERED_PATH.write_text(json.dumps(DISCOVERED, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"[discovered] 已写出 {DISCOVERED_PATH}", flush=True)
    except Exception as e:
        print(f"[discovered] 写出失败（忽略）: {e}", flush=True)


# 降级样例：真实抓取失败时用它保证链路出「前 N 条 FDE 人才概括信息」的产物。
# 「招聘管理」里关键词搜的是**人才**，非会员通常只能看到约 3 条、且只有对人才的概括性描述
# （姓名常被打码、所在公司常隐藏，主要是职位/经验/行业的概括）。明确标注 source=sample，绝不冒充真实数据。
SAMPLE_TALENTS = [
    {"name": "匿名用户", "headline": "资深交付工程师 / Forward Deployed Engineer",
     "company": "某大模型公司（已隐藏）", "experience": "8 年经验", "location": "北京",
     "summary": "负责大模型客户侧落地交付，熟悉 Python / Agent 工程，带过交付小队。",
     "tags": ["FDE", "大模型", "交付"], "url": ""},
    {"name": "脉脉用户", "headline": "解决方案 / 前沿部署工程师",
     "company": "某 AI Infra 公司（已隐藏）", "experience": "5 年经验", "location": "上海",
     "summary": "AI 基础设施方向，擅长客户现场部署与问题排查，具备 SRE 背景。",
     "tags": ["FDE", "解决方案", "SRE"], "url": ""},
    {"name": "匿名用户", "headline": "客户工程 / Field/Forward Deployed Engineer",
     "company": "某云厂商（已隐藏）", "experience": "6 年经验", "location": "深圳",
     "summary": "云产品客户交付与咨询，熟悉数据链路与现场实施，英语可作为工作语言。",
     "tags": ["FDE", "云", "咨询"], "url": ""},
]



def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_state(phase: str, **extra) -> None:
    """原子写 state.json：先写临时文件再 rename，避免编排侧读到半截 JSON。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"phase": phase, "keyword": SEARCH_KEYWORD, "top_n": TOP_N, "updated_at": _now()}
    payload.update(extra)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)
    print(f"[state] {phase} {json.dumps(extra, ensure_ascii=False)}", flush=True)


def _first_matching(page, selectors: list[str], timeout_ms: int = 1500):
    """按候选选择器顺序找第一个存在的元素；都找不到返回 None（不抛）。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=timeout_ms)
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def switch_to_qr(page) -> bool:
    """脉脉登录页默认是手机验证码登录；点右上角图标切到扫码视图。点不到返回 False（不抛）。"""
    el = _first_matching(page, SELECTORS["qr_switch"], timeout_ms=5000)
    if el is None:
        return False
    try:
        el.click(timeout=3000)
        time.sleep(1.5)  # 给切换动画 + canvas 渲染一点时间
        return True
    except Exception:
        return False


def capture_qr(page) -> bool:
    """把登录二维码截到 qrcode.png。脉脉二维码是 <canvas>，优先截该元素；失败整页兜底。"""
    canvas = _first_matching(page, SELECTORS["qr_canvas"], timeout_ms=6000)
    try:
        if canvas is not None:
            canvas.screenshot(path=str(QR_PATH))
        else:
            page.screenshot(path=str(QR_PATH), full_page=False)
        return True
    except Exception:
        try:
            page.screenshot(path=str(QR_PATH), full_page=False)
            return True
        except Exception:
            return False


def detect_manual_verify(page) -> bool:
    """检测是否出现「扫码之外的额外验证」（滑块/短信/人脸等）。命中即截 verify.png。"""
    el = _first_matching(page, SELECTORS["manual_verify_marker"], timeout_ms=800)
    if el is None:
        return False
    try:
        page.screenshot(path=str(VERIFY_PATH), full_page=False)
    except Exception:
        pass
    return True


def _page_derailed(page) -> bool:
    """页面是否被「清场」：停在 about:blank / chrome:// / 错误页 / 非 maimai 域。

    沙箱 headless 环境里反爬可能把登录页导航走（BOSS 曾被打到 about:blank / chrome://new-tab-page）。
    这种情况既不是登录成功、也不是正常登录页，需单独识别，避免误判为已登录。
    """
    try:
        url = page.url or ""
    except Exception:
        return True
    if not url or url == "about:blank":
        return True
    if url.startswith("about:") or url.startswith("chrome") or url.startswith("data:"):
        return True
    return "maimai.cn" not in url


def is_logged_in(page) -> bool:
    """登录成功判定（严格）：必须同时满足——

      1) 停在真实 maimai.cn 页面（不是 about:blank / chrome:// / 错误页 / 其它域）；
      2) URL 已离开登录页（不含 /login，也不含 /platform/login）；
      3) 登录表单 / 扫码切换控件已消失（还在就说明仍在登录页，没登进去）。

    之所以要第 3 条：沙箱里反爬可能把页面导航走，使 URL 不再含 /login，但这不是登录成功。
    只有"离开登录 URL 且登录控件消失且仍在 maimai"三者同时成立，才认定登录成功。
    """
    if _page_derailed(page):
        return False
    try:
        url = page.url or ""
    except Exception:
        return False
    if ("/login" in url) or (LOGIN_URL_MARK in url):
        return False
    # 登录控件还在 = 仍未登录。
    try:
        if page.locator("div.p-login-switch").first.count() > 0:
            return False
        if page.locator("canvas").first.count() > 0 and "search" not in url and "job" not in url:
            # 还停在扫码页（canvas 二维码在、且不是职位/搜索页）。
            return False
    except Exception:
        pass
    return True


def dump_job_page(page, screenshot: bool = True) -> None:
    """登录后把职位搜索页的 DOM 快照 + 纯文本（+ 可选截图）落盘。

    其中 job_page.txt（body innerText）是关键：编排侧在选择器抓不到职位时，会把这段文本
    喂给模型抽取职位。所以这里要**尽力保证 txt 一定落盘**（哪怕截图/HTML 失败），
    让「模型兜底解析」这条路始终有输入。

    screenshot=False 时只存 HTML/文本、跳过截图：用于「抢在 /ent 新标签页被关前」快速留证，
    避免 full_page 截图在无限滚动的重 SPA 上卡住（上一轮 searching 卡死的直接诱因）。
    截图统一用**可视区**（full_page 在 /ent 长页上极易超长/卡顿，且精调选择器只需 DOM/HTML）。
    """
    if screenshot:
        try:
            page.screenshot(path=str(JOB_PAGE_PNG), full_page=False, timeout=8000)
        except Exception:
            pass
    try:
        html = page.content()
        JOB_PAGE_HTML.write_text(html, encoding="utf-8")
    except Exception:
        pass
    try:
        text = page.inner_text("body", timeout=5000)
        JOB_PAGE_TXT.write_text(text or "", encoding="utf-8")
    except Exception:
        # inner_text 拿不到就退而用 DOM 文本兜底，仍尽量写出点东西供模型解析。
        try:
            JOB_PAGE_TXT.write_text((page.content() or "")[:200000], encoding="utf-8")
        except Exception:
            pass


def _is_homepage(url: str) -> bool:
    """URL 是否是脉脉营销首页（path 为空或 /）。候选职位 URL 失效时会被导回这里。"""
    try:
        from urllib.parse import urlparse
        p = urlparse(url or "")
    except Exception:
        return False
    if "maimai.cn" not in (p.netloc or ""):
        return False
    path = (p.path or "").rstrip("/")
    return path in ("", "/")


def try_search_box(page, keyword: str) -> bool:
    """在招聘页尝试定位搜索框，输入关键词并回车。命中并输入成功返回 True，否则 False（不抛）。

    不写死脉脉的搜索接口/参数——直接驱动页面上的搜索输入框，页面改版也更扛得住。
    选择器优先用学习库（learned_selectors.json 的 search_box.selectors），再用内置兜底。
    命中的选择器会记进 DISCOVERED 供回填。
    """
    search_selectors = learned_list("search_box", "selectors") or [
        "input[type='search']",
        "input[placeholder*='搜索']",
        "input[placeholder*='职位']",
        "input[placeholder*='公司']",
        "input[class*='search']",
        ".search-input input",
        "input[type='text']",
    ]
    for sel in search_selectors:
        try:
            box = page.locator(sel).first
            box.wait_for(state="attached", timeout=2500)
            if box.count() == 0:
                continue
            box.click(timeout=3000)
            box.fill(keyword, timeout=3000)
            box.press("Enter", timeout=3000)
            time.sleep(3)  # 等搜索结果异步渲染。
            record_discovered("search_box_selector", sel)
            return True
        except Exception:
            continue
    return False


def collect_nav_anchors(page) -> list[dict]:
    """抓登录后页面所有 <a> 的 text+href+target，落盘 nav_anchors.json。

    这是「学习回填」的原料：编排侧/人能据此看清招聘入口的真实 href、是不是新标签页，
    从而把正确的 href/选择器固化回学习库。顺便把整页 HTML dump 到 post_login.html。
    """
    anchors: list[dict] = []
    try:
        anchors = page.eval_on_selector_all("a", """
            els => els.map(e => ({
                text: (e.innerText || e.textContent || '').trim().slice(0, 40),
                href: e.getAttribute('href') || '',
                target: e.getAttribute('target') || ''
            })).filter(a => a.href || a.text)
        """) or []
    except Exception as e:
        print(f"[nav] 收集 anchors 失败: {e}", flush=True)
    try:
        NAV_ANCHORS_JSON.write_text(
            json.dumps({"url": page.url or "", "anchors": anchors}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass
    try:
        POST_LOGIN_HTML.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    return anchors


def _find_recruit_href(anchors: list[dict]) -> str:
    """从 anchors 里挑出「招聘管理」入口的真实 href。先按导航文案匹配（优先『招聘管理』），再按 href 关键词兜底。"""
    nav_texts = learned_list("recruit_nav", "nav_text") or ["招聘管理", "招聘", "找人才", "人才"]
    href_kws = learned_list("recruit_nav", "href_candidates") or ["/ent", "/zp", "/recruit", "/talent"]
    # 1) 文案精确/包含匹配。nav_texts 已把「招聘管理」排在最前，优先命中 HR/B 端人才搜索入口。
    for a in anchors:
        t = (a.get("text") or "").strip()
        if t and any(t == nt or nt in t for nt in nav_texts) and a.get("href"):
            return a["href"]
    # 2) href 关键词兜底。
    for a in anchors:
        h = a.get("href") or ""
        if any(kw in h for kw in href_kws):
            return h
    return ""


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://maimai.cn" + href
    return "https://maimai.cn/" + href


def _landed_ok(url: str) -> bool:
    """URL 是否落到了「有效的招聘页」：既不在登录页、也不是营销首页。"""
    if not url:
        return False
    if (LOGIN_URL_MARK in url) or ("/login" in url):
        return False
    return not _is_homepage(url)


def _try_goto_recruit(page, url_abs: str, settle_s: float = 3.0) -> bool:
    """goto 招聘 URL；若被弹回登录页，settle 后重试一次（刚登录 cookie 可能未对该入口生效）。

    实测：登录成功后立刻直接 goto 搜人才页，会被脉脉二次鉴权弹回 /platform/login；
    等 cookie settle 再 goto 常能通，故这里带一次「被弹回→等待→重试」。
    """
    for attempt in range(2):
        try:
            page.goto(url_abs, wait_until="domcontentloaded", timeout=20000)
            time.sleep(settle_s)
            cur = page.url or ""
            if _landed_ok(cur):
                return True
            if attempt == 0:
                print(f"[recruit] goto 被弹回 {cur}，settle 后重试一次", flush=True)
                time.sleep(4)
        except Exception as e:
            print(f"[recruit] goto {url_abs} 失败: {e}", flush=True)
            break
    return False


def _live_page(context, candidate):
    """返回一个「还活着」的页对象：candidate 若已关闭，就从 context.pages 取一个未关闭的页，
    且**优先挑 URL 在 /ent 招聘域下的页**（避免退回社区首页抓错内容）。

    脉脉招聘入口打开的 target=_blank 新标签页，在 headless 下可能被站点脚本几秒后关闭/替换；
    每步操作前统一用它兜底，避免在已关闭的页上操作抛 'Target page … has been closed'。
    """
    try:
        if candidate is not None and not candidate.is_closed():
            return candidate
    except Exception:
        pass
    live = []
    try:
        for p in reversed(context.pages):
            try:
                if not p.is_closed():
                    live.append(p)
            except Exception:
                continue
    except Exception:
        pass
    # 优先返回停在 /ent 招聘域的活页（人才搜索页），其次任意活页，最后退回 candidate。
    for p in live:
        try:
            if "/ent" in (p.url or ""):
                return p
        except Exception:
            continue
    return live[0] if live else candidate


def _extract_cards(work, cards, hit_sel: str, limit: int) -> list[dict]:
    """从已定位的人才卡片 locator 里抽前 limit 条字段，返回 talent dict 列表（全程防抛）。

    抽取逻辑与 scrape_talents 末段共用：优先字段子选择器，抽不到就用整卡文本兜底
    （第一行当标题、整段当概括）。source=live。"""
    def _joined(path_key: str, fallback: str) -> str:
        lst = learned_list("talent_fields", path_key)
        return ", ".join(lst) if lst else fallback

    headline_sel = _joined("headline", "[class*='title'], [class*='name'], [class*='position'], h3, h4")
    company_sel = _joined("company", "[class*='company'], [class*='corp'], [class*='org']")
    experience_sel = _joined("experience", "[class*='exp'], [class*='year'], [class*='work']")
    location_sel = _joined("location", "[class*='city'], [class*='location'], [class*='area']")
    summary_sel = _joined("summary", "[class*='desc'], [class*='summary'], [class*='intro'], p")

    out: list[dict] = []
    try:
        count = min(cards.count(), limit)
    except Exception:
        return out
    for i in range(count):
        try:
            c = cards.nth(i)

            def _txt(sub: str) -> str:
                try:
                    e = c.locator(sub).first
                    if e.count() > 0:
                        return (e.inner_text(timeout=1200) or "").strip()
                except Exception:
                    pass
                return ""

            headline = _txt(headline_sel)
            company = _txt(company_sel)
            experience = _txt(experience_sel)
            location = _txt(location_sel)
            summary = _txt(summary_sel)
            try:
                whole = (c.inner_text(timeout=1200) or "").strip()
            except Exception:
                whole = ""
            lines = [ln.strip() for ln in whole.split("\n") if ln.strip()]
            if not headline and lines:
                headline = lines[0][:60]
            if not summary:
                summary = whole[:300]
            if not (headline or summary):
                continue
            out.append({
                "name": "", "headline": headline, "company": company,
                "experience": experience, "location": location, "summary": summary,
                "tags": [], "url": "", "source": "live",
            })
        except Exception:
            continue
    if out:
        record_discovered("talent_card_selector", hit_sel)
    return out


def _fast_grab(work, context, max_s: float = 7.0) -> list[dict]:
    """「落地即极速抓取」：新标签页几秒内会被站点反 headless 脚本杀掉，故不 settle、不等 networkidle，
    一拿到落地页就在一个短轮询里反复 dump + 扫卡片，赌被杀前那几秒窗口。全程防 Target closed。

    返回抓到的人才列表（可能为空）。同时顺手把能 dump 的 DOM/文本落盘，供失败后 LLM 兜底。"""
    card_selectors = learned_list("talent_card", "selectors") or SELECTORS["talent_card"]
    deadline = time.time() + max_s
    grabbed: list[dict] = []
    # 关键（本轮核心修正）：新标签页落地的是 /ent/index（ent 首页/社区流），**不是**人才搜索页。
    # 但这个 ent 标签页持有有效 ent 会话——在它内部**同标签页** goto 到人才搜索页 + ?query=FDE
    # 是被承认的（用户已确认网页上 search_v2 是同标签页跳转）；而从社区页直接 goto 会被打回登录。
    # 故：只要落在 /ent 但还没到 talents/search_v2，就在这个 tab 里同标签页跳到搜人才页并带 FDE。
    try:
        cur0 = work.url or ""
        on_talent = ("search_v2" in cur0) or ("talents/discover" in cur0)
        if ("/ent" in cur0) and not on_talent:
            from urllib.parse import quote
            target_url = f"https://maimai.cn/ent/talents/discover/search_v2?query={quote(SEARCH_KEYWORD)}"
            work.goto(target_url, wait_until="commit", timeout=8000)
            record_discovered("fast_search_url", target_url)
            print(f"[recruit] ent 标签页内同标签页跳转到人才搜索页: {target_url}", flush=True)
            time.sleep(2)  # 给 SPA 一点渲染时间，但不长等（赌窗口）
        elif on_talent and "query=" not in cur0:
            from urllib.parse import quote
            base = cur0.split("?")[0]
            work.goto(f"{base}?query={quote(SEARCH_KEYWORD)}", wait_until="commit", timeout=8000)
            record_discovered("fast_search_url", f"{base}?query={quote(SEARCH_KEYWORD)}")
    except Exception as e:
        print(f"[recruit] 同标签页跳搜人才页失败（忽略，继续抓当前页）: {e}", flush=True)
    while time.time() < deadline:
        work = _live_page(context, work)
        if work is None:
            break
        try:
            if work.is_closed():
                break
        except Exception:
            break
        # 每轮先抢存一份 DOM/文本（短超时，别卡住窗口）。
        try:
            dump_job_page(work, screenshot=False)
        except Exception:
            pass
        # 只在**人才搜索页**上抓，避免把社区流/招聘首页的 feed 卡片误当人才（上一轮 degraded=false
        # 却抓回一堆『同事圈』吐槽的真因）。还没到人才页就先不抓，等本轮内的同标签页跳转生效。
        try:
            cur = work.url or ""
        except Exception:
            cur = ""
        on_talent_page = ("search_v2" in cur) or ("talents/discover" in cur) or ("talents" in cur)
        if not on_talent_page:
            time.sleep(0.6)
            continue
        # 立刻扫一遍人才卡片。
        for sel in card_selectors:
            try:
                loc = work.locator(sel)
                if loc.count() > 0:
                    grabbed = _extract_cards(work, loc, sel, TOP_N)
                    if grabbed:
                        break
            except Exception:
                continue
        if grabbed:
            break
        time.sleep(0.6)
    return grabbed


def open_recruit_page(page, context):
    """登录后进入「招聘/招聘管理」人才搜索页，返回真正落地的页对象（可能是新标签页）或 None。

    实测结论（本轮 dump 验证，关键）：登录后社区页（/community/home/recommended）顶部导航
    「社区 / 招聘 / 企业号 / 商业服务 ...」里的「招聘」**不是 <a href>**——整页只有 6 个 <a>，没有一个
    指向 /ent 或 /zp。它是 React 挂了 click 事件的 <span>/<div>/<li>。所以：
      * 旧的 `a:has-text('招聘')` / `a[href*='/ent']` 永远匹配不到。
      * 直接 `page.goto('…/ent/talents/discover/search_v2')` 会被 referer/手势校验重定向到首页、
        再打回 /platform/login（上一轮 sandbox.log 坐实：url=https://maimai.cn/ → /platform/login）。
    正确姿势：**按文案点击顶部导航的「招聘」元素（任意标签，不限 <a>）**，走 SPA 客户端跳转——
    带用户手势、同 context，登录态天然保留，不会被弹回登录页。点击若开新标签页则接住，否则原地跳转。

    返回值：成功 → 落地的页对象（在它上面继续搜索/抓取）；失败 → None。
    """
    collect_nav_anchors(page)
    # 文案候选：先「招聘管理」（B 端 HR 入口），再「招聘」「找人才」「人才」。:text-is 要求精确文本，
    # 避免匹配到动态流里正文包含「招聘」的长段落；用 header/nav 作用域进一步收窄到顶部导航。
    nav_texts = learned_list("recruit_nav", "nav_text") or ["招聘管理", "招聘", "找人才", "人才"]
    # 既保留学习库里学到的 anchor_selectors（历史回填），也补上「任意标签按精确文案」的候选。
    text_selectors: list[str] = []
    for scope in ("header", "nav", ""):
        for t in nav_texts:
            prefix = f"{scope} " if scope else ""
            text_selectors.append(f"{prefix}:text-is('{t}')")
    click_selectors = learned_list("recruit_nav", "anchor_selectors") + text_selectors

    # 实测结论（已反复坐实，务必保留）：登录态只在「点击 target=_blank 打开的新标签页」里有效。
    #   * 把入口改 _self 原地跳 / 直接 page.goto(search_v2) —— 都被二次鉴权打回 /platform/login。
    #   * 只有 window.open 开出来的新标签页，才带着有效 cookie 建起 /ent 招聘会话。
    # 代价：该新标签页会在几秒内被站点反 headless 脚本主动关掉。故策略改为「新标签页 + 极速抓取」：
    #   接住新标签页后，落地 /ent 的第一时间**不 settle、不等 networkidle**，立刻 dump + 立刻扫卡片，
    #   赌标签页被杀前那几秒窗口把 3 条人才概括信息抓出来（全部包 try，防 Target closed）。
    for sel in click_selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=2500)
            if loc.count() == 0:
                continue
            new_page = None
            try:
                # 点击可能①原地 SPA 跳转 ②window.open 开新标签页。两种都兜住。
                with context.expect_page(timeout=4000) as pinfo:
                    loc.click(timeout=3000)
                new_page = pinfo.value
            except Exception:
                # 没弹新标签页：大概率原地 SPA 跳转，给前端一点路由+渲染时间。
                time.sleep(3)
            target = _live_page(context, new_page) if new_page is not None else page
            try:
                target.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            time.sleep(3)
            target = _live_page(context, target)
            cur = ""
            try:
                cur = target.url or ""
            except Exception:
                cur = ""
            if _landed_ok(cur):
                record_discovered("recruit_click_selector", sel)
                if new_page is not None:
                    record_discovered("recruit_opened_new_tab", True)
                record_discovered("recruit_href", cur)
                # 抢在 /ent 新标签页被站点脚本关掉前，第一时间 dump + 极速抓取。
                # 上一轮就是进了 search_v2 但标签页随即被关，DOM 没抓到就崩（Target closed）。
                if "/ent" in cur:
                    global FAST_GRABBED
                    try:
                        FAST_GRABBED = _fast_grab(target, context, max_s=22.0)
                        if FAST_GRABBED:
                            print(f"[recruit] 落地即极速抓取命中 {len(FAST_GRABBED)} 条人才", flush=True)
                        else:
                            print("[recruit] 落地极速抓取未命中卡片（标签页可能被秒杀）", flush=True)
                    except Exception as e:
                        print(f"[recruit] 极速抓取异常（忽略）: {e}", flush=True)
                return target
            print(f"[recruit] 点击 {sel} 后落地 {cur}，未进招聘页，换下一个", flush=True)
        except Exception:
            continue

    # 兜底：goto 已知直达 URL（通常会被重定向回登录页，仅极少数时序/账号下有效）。
    candidates = ["https://maimai.cn/ent/talents/discover/search_v2",
                  "https://maimai.cn/ent/talents/discover"]
    try:
        node = LEARNED.get("recruit_nav") if isinstance(LEARNED, dict) else None
        if isinstance(node, dict) and node.get("resolved_href"):
            candidates.insert(0, _abs_url(node["resolved_href"]))
    except Exception:
        pass
    for url in candidates:
        if _try_goto_recruit(page, url):
            record_discovered("recruit_href", url)
            return page
    return None



def _settle_spa(work, label: str = "", idle_timeout_ms: int = 8000, extra_sleep_s: float = 2.5) -> None:
    """等 SPA 真正渲染出来：先 networkidle（拿不到就忽略），再固定小睡给前端一点绘制时间。

    实测：脉脉人才搜索页 /ent/talents/discover 是重前端 SPA，domcontentloaded 后 DOM 还是空壳
    + 「加载中」，太快去抓卡片必然抓空。故每次导航/搜索后都 settle 一下再抓。
    """
    try:
        work.wait_for_load_state("networkidle", timeout=idle_timeout_ms)
    except Exception:
        pass
    time.sleep(extra_sleep_s)
    if label:
        try:
            print(f"[settle] {label} url={work.url or ''}", flush=True)
        except Exception:
            pass


def _wait_for_cards(work, card_selectors: list[str], total_s: float = 22.0):
    """轮询等待人才卡片真正渲染出来，返回 (locator, hit_selector) 或 (None, "")。

    不是单次扫描：SPA 搜索结果异步回来，卡片可能在数秒后才挂上 DOM。每轮把所有候选选择器
    过一遍，命中非空就立即返回；全部没命中就小睡后再来一轮，直到 total_s 用尽。
    """
    deadline = time.time() + total_s
    while time.time() < deadline:
        for sel in card_selectors:
            try:
                loc = work.locator(sel)
                if loc.count() > 0:
                    # 命中后再确认第一张已挂载，避免拿到占位空节点。
                    loc.first.wait_for(state="attached", timeout=1500)
                    return loc, sel
            except Exception:
                continue
        time.sleep(1.5)
    return None, ""


def scrape_talents(page, context) -> list[dict]:
    """登录后进入「招聘管理」→ 关键词搜**人才** → 抓前 TOP_N 条人才概括信息。自愈三层：

      1) 进招聘管理页：open_recruit_page（固化 href → 实时读真实 href goto → 点击接住 target=_blank 新标签页）。
         脉脉顶部「招聘」入口可能是 target=_blank 新标签页，所以可能返回一个新 tab（挂在 page._recruit_tab）。
      2) 搜索：在招聘管理页用页面搜索框输入关键词回车搜人才（try_search_box，不猜 query 格式）。
      3) 抓取：用学习库 talent_card.selectors 优先、内置兜底定位人才卡片；命中选择器记进 DISCOVERED。
         非会员通常只能看到约 3 条、且只有对人才的概括性描述（标题/经验/行业概括，姓名公司常被隐藏）。
    任一步彻底失败都 dump 现场（talent_page.* + nav_anchors + post_login.html）后抛出，
    由上层转降级 + 编排侧用 LLM 读真实 DOM 抽取 / 推断选择器回填。
    """
    # open_recruit_page 返回「真正落地的页对象」（可能是点击打开的 target=_blank 新标签页）或 None。
    # 后续 settle/搜索/抓取/dump 必须全部作用在这个落地页上，否则抓的还是主 page（社区页）。
    work = open_recruit_page(page, context)

    # 「落地即极速抓取」已在 open_recruit_page 里趁新标签页被秒杀前抢到人才：直接用，不再走
    # 后续 settle/搜索/等卡片——那条路在新标签页上几乎必然被 Target closed 打断。
    if FAST_GRABBED:
        print(f"[scrape] 采用落地极速抓取结果 {len(FAST_GRABBED)} 条", flush=True)
        return FAST_GRABBED[:TOP_N]

    if work is None:
        # 没进成招聘管理页：仍把当前社区页 dump 出来（talent_page.* + nav_anchors 供离线学习），再抛降级。
        collect_nav_anchors(page)
        dump_job_page(page)
        raise RuntimeError("未能进入招聘管理页（入口可能是新标签页/需额外权限；已 dump nav_anchors 供学习）")

    # 进到人才搜索页后，/ent/talents/discover 是重前端 SPA，必须先等它真正渲染出来再操作，
    # 否则页面还是空壳 +「加载中」，搜索框/人才卡片都抓不到（上一轮降级的真因）。
    work = _live_page(context, work)
    _settle_spa(work, label="进入人才搜索页")

    # 搜人才：优先用页面搜索框输入关键词回车；找不到搜索框就退而用 URL query 直达搜索结果。
    work = _live_page(context, work)
    searched = try_search_box(work, SEARCH_KEYWORD)
    if not searched:
        print("[scrape] 未命中页面搜索框，改用 URL query 直达搜索结果。", flush=True)
        base = (work.url or "https://maimai.cn/ent/talents/discover/search_v2").split("?")[0]
        from urllib.parse import quote
        for q in (f"{base}?query={quote(SEARCH_KEYWORD)}",
                  f"{base}?keyword={quote(SEARCH_KEYWORD)}",
                  f"{base}?q={quote(SEARCH_KEYWORD)}"):
            try:
                work.goto(q, wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)
                if _landed_ok(work.url or ""):
                    searched = True
                    record_discovered("search_url", q)
                    break
            except Exception:
                continue
    # 搜索结果异步回来，再 settle 一次，等结果列表渲染。
    work = _live_page(context, work)
    _settle_spa(work, label="搜索结果渲染")

    card_selectors = learned_list("talent_card", "selectors") or SELECTORS["talent_card"]
    # 轮询等待卡片真正挂上 DOM（SPA 结果可能晚几秒才渲染），而非单次扫描即判失败。
    work = _live_page(context, work)
    cards, hit_sel = _wait_for_cards(work, card_selectors, total_s=22.0)
    # 卡片等出来后再 dump（这次 DOM 更全，含搜索结果），给 LLM 兜底留带结果的真实页面。
    # 「抢先 dump」已在 open_recruit_page 里留过早期快照，这里不再重复 dump 一遍。
    work = _live_page(context, work)
    collect_nav_anchors(work)
    dump_job_page(work)
    if cards is None or cards.count() == 0:
        raise RuntimeError("未找到人才卡片（页面结构未匹配或被风控；已 dump talent_page.html / post_login.html 供精调）")
    record_discovered("talent_card_selector", hit_sel)

    # 字段子选择器也走学习库（拼成 CSS 逗号组合）。人才卡片字段：标题/经验/行业/概括描述。
    def _joined(path_key: str, fallback: str) -> str:
        lst = learned_list("talent_fields", path_key)
        return ", ".join(lst) if lst else fallback

    headline_sel = _joined("headline", "[class*='title'], [class*='name'], [class*='position'], h3, h4")
    company_sel = _joined("company", "[class*='company'], [class*='corp'], [class*='org']")
    experience_sel = _joined("experience", "[class*='exp'], [class*='year'], [class*='work']")
    location_sel = _joined("location", "[class*='city'], [class*='location'], [class*='area']")
    summary_sel = _joined("summary", "[class*='desc'], [class*='summary'], [class*='intro'], p")

    talents: list[dict] = []
    count = min(cards.count(), TOP_N)
    for i in range(count):
        c = cards.nth(i)

        def _txt(sub: str) -> str:
            try:
                e = c.locator(sub).first
                if e.count() > 0:
                    return (e.inner_text(timeout=1500) or "").strip()
            except Exception:
                pass
            return ""

        def _self_text() -> str:
            try:
                return (c.inner_text(timeout=1500) or "").strip()
            except Exception:
                return ""

        headline = _txt(headline_sel)
        company = _txt(company_sel)
        experience = _txt(experience_sel)
        location = _txt(location_sel)
        summary = _txt(summary_sel)
        whole = _self_text()
        # 兜底：卡片子结构没命中就用整卡文本——第一行当标题、整段当概括描述，保证不至于全空。
        lines = [ln.strip() for ln in whole.split("\n") if ln.strip()]
        if not headline and lines:
            headline = lines[0][:60]
        if not summary:
            # 非会员卡片本来就是「概括性描述」，整卡文本即视为概括信息。
            summary = whole[:300]

        talents.append({
            "name": "",  # 非会员一般看不到真实姓名，留空而非编造
            "headline": headline,
            "company": company,
            "experience": experience,
            "location": location,
            "summary": summary,
            "tags": [],
            "url": "",
            "source": "live",
        })
    if not talents:
        raise RuntimeError("人才卡片解析为空")
    return talents


def save_results(talents: list[dict], degraded: bool, reason: str = "") -> None:
    RESULT_JSON.write_text(json.dumps({
        "keyword": SEARCH_KEYWORD,
        "count": len(talents),
        "degraded": degraded,
        "reason": reason,
        "saved_at": _now(),
        "talents": talents,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 脉脉「{SEARCH_KEYWORD}」人才 Top {len(talents)}（招聘管理·非会员可见的概括信息）", ""]
    if degraded:
        lines += [f"> ⚠️ 降级样例数据（真实抓取失败：{reason}）。链路已跑通，数据仅供演示。", ""]
    for i, t in enumerate(talents, 1):
        tags = " / ".join(t.get("tags") or [])
        lines.append(f"## {i}. {t.get('headline','(无标题)') or '(无标题)'}")
        if t.get("name"):
            lines.append(f"- 姓名：{t.get('name')}")
        lines.append(f"- 公司：{t.get('company','') or '-'}")
        lines.append(f"- 经验：{t.get('experience','') or '-'}")
        lines.append(f"- 地点：{t.get('location','') or '-'}")
        if t.get("summary"):
            lines.append(f"- 概括：{t.get('summary')}")
        if tags:
            lines.append(f"- 标签：{tags}")
        lines.append("")
    RESULT_MD.write_text("\n".join(lines), encoding="utf-8")


def run() -> int:
    write_state("starting")
    load_learned()  # 先加载自愈选择器库（挂载在 uploads 下的 learned_selectors.json）
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        # Playwright 没装好：直接降级产出样例，保证链路有产物。
        write_state("searching", degraded=True, reason=f"playwright 不可用: {e}")
        save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason=f"playwright import 失败: {e}")
        write_state("done", degraded=True, reason="playwright 不可用，已输出样例数据",
                    result_file="result.json")
        return 0

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(
                locale="zh-CN",
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
                viewport={"width": 1280, "height": 900},
                # 方舟沙箱出网经安全拦截层做 TLS 检查，Chromium 校验 maimai.cn 证书链会报
                # ERR_CERT_AUTHORITY_INVALID。忽略证书错误让真实页面能打开（演示场景可接受）。
                ignore_https_errors=True,
            )
            # 全局护栏：给所有操作设默认超时，防止 /ent 重 SPA 页在加载/被关时让 content()/
            # eval_on_selector_all 等**无限阻塞**（上一轮 searching 卡死 9 分钟的真因）。到点抛异常，
            # 由各处 try/except 兜住转降级，绝不让整个脚本挂死不再写 state。
            ctx.set_default_timeout(15000)
            ctx.set_default_navigation_timeout(30000)
            page = ctx.new_page()

            # 1) 打开登录页（会跳转到 /platform/login），切到扫码视图并截二维码。
            try:
                page.goto(MAIMAI_LOGIN, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            except Exception as e:
                write_state("error", degraded=True, reason=f"打开登录页失败: {e}")
                save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason=f"打开登录页失败: {e}")
                write_state("done", degraded=True, reason="登录页不可达，已输出样例数据",
                            result_file="result.json")
                return 0

            switched = switch_to_qr(page)  # 切不到也继续（有的版本默认就是扫码或布局不同）
            if not capture_qr(page):
                write_state("error", degraded=True, reason="截取二维码失败")
                save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason="截取二维码失败")
                write_state("done", degraded=True, reason="无法截二维码，已输出样例数据",
                            result_file="result.json")
                return 0

            qr_version = 1
            write_state("waiting_qr_scan", qr_file="qrcode.png", switched_to_qr=switched,
                        page_url=(page.url or ""), qr_version=qr_version,
                        hint="请在 MA 客户端打开二维码并用脉脉 App 扫码登录",
                        timeout_s=LOGIN_TIMEOUT_S)

            # 2) 轮询：登录成功 or 出现额外验证 or 超时。
            #    每个 tick 把实时 page.url + 是否被清场写进 state.json，作为沙箱里页面行为的硬证据。
            #    脉脉二维码约 2 分钟失效，故每隔 QR_REFRESH_S 主动重载登录页换新码，并把 qr_version +1，
            #    编排侧据 qr_version 变化重新拉取 qrcode.png 给用户。
            deadline = time.time() + LOGIN_TIMEOUT_S
            logged = False
            last_reported_url = page.url or ""
            next_qr_refresh = time.time() + QR_REFRESH_S
            while time.time() < deadline:
                cur_url = page.url or ""
                derailed = _page_derailed(page)

                if is_logged_in(page):
                    logged = True
                    break
                if detect_manual_verify(page):
                    write_state("need_manual_verify", verify_file="verify.png", page_url=cur_url,
                                hint="出现扫码之外的额外验证（滑块/短信/人脸等），已暂停，请人工处理后告知")
                    # 暂停：持续观察，一旦人工在客户端处理完、页面进入已登录即继续。
                    while time.time() < deadline:
                        if is_logged_in(page):
                            logged = True
                            break
                        time.sleep(POLL_INTERVAL_S)
                    break

                # 页面被清场，或到了二维码刷新时点：重载登录页 + 切扫码，换一张全新有效二维码。
                need_refresh = derailed or (time.time() >= next_qr_refresh)
                if need_refresh:
                    try:
                        page.goto(MAIMAI_LOGIN, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                        switch_to_qr(page)
                    except Exception:
                        pass
                    next_qr_refresh = time.time() + QR_REFRESH_S
                    qr_version += 1
                    cur_url = page.url or ""

                # 重截二维码（刷新后是新码；未刷新则维持当前码的最新截图）。
                try:
                    capture_qr(page)
                except Exception:
                    pass

                # URL 变化 / 被清场 / 二维码刷新时，写一次 waiting 状态，带上实时证据与 qr_version。
                if need_refresh or cur_url != last_reported_url or derailed:
                    write_state("waiting_qr_scan", qr_file="qrcode.png",
                                page_url=cur_url, derailed=derailed, qr_version=qr_version,
                                hint=("⚠️ 登录页被导航走（疑似沙箱反爬清场），已重载并刷新二维码" if derailed
                                      else ("二维码已刷新，请扫最新的那张" if need_refresh
                                            else "请在 MA 客户端打开二维码并用脉脉 App 扫码登录")),
                                timeout_s=LOGIN_TIMEOUT_S)
                    last_reported_url = cur_url

                time.sleep(POLL_INTERVAL_S)

            if not logged:
                write_state("error", degraded=True, reason="登录超时或未完成")
                save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason="登录超时")
                write_state("done", degraded=True, reason="登录未完成，已输出样例数据",
                            result_file="result.json")
                return 0

            write_state("logged_in", hint="登录成功，开始搜索职位")

            # 诊断：扫码后浏览器落到哪一页？记录真实 URL + 落地页文本，判断是「真登录（个人 feed）」
            # 还是「假登录（被导去营销首页/又弹回登录页）」。这是定位脉脉 headless 会话是否有效的硬证据。
            try:
                landing_url = page.url or ""
                landing_text = page.inner_text("body", timeout=5000) or ""
                (OUTPUT_DIR / "post_login.txt").write_text(
                    f"POST_LOGIN_URL: {landing_url}\n\n{landing_text}", encoding="utf-8")
                print(f"[diag] post-login url={landing_url} text_len={len(landing_text)}", flush=True)
            except Exception as e:
                print(f"[diag] dump post-login failed: {e}", flush=True)

            # 注：早期版本登录后先跑 probe_recruit_paths() 探测多条候选路径取证，但它会卡住主流程
            # （直达 /ent/talents/... 被弹回登录页、点「招聘」新标签页原地不跳转都要各等几秒），
            # 导致产物停在 probe_*.txt、迟迟进不到真正的「招聘管理→搜人才」。现已移除，直接进主流程。

            # 3) 进「招聘管理」→ 搜人才 → 抓前 N 条人才概括信息；失败降级（但无论如何会 dump 页面结构）。
            write_state("searching", keyword=SEARCH_KEYWORD)
            try:
                talents = scrape_talents(page, ctx)
                save_results(talents, degraded=False)
                flush_discovered()  # 把真实学到的 href/选择器落盘（编排侧合并回学习库）
                write_state("done", degraded=False, count=len(talents), result_file="result.json")
            except Exception as e:
                flush_discovered()  # 失败也落盘已学到的东西（哪怕只学到招聘管理 href）
                save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason=f"搜索/解析失败: {e}")
                write_state("done", degraded=True, reason=f"搜索/解析失败: {e}",
                            count=TOP_N, result_file="result.json")
            return 0
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass


def main() -> int:
    try:
        return run()
    except Exception as e:
        tb = traceback.format_exc()
        try:
            write_state("error", degraded=True, reason=str(e), traceback=tb[-1500:])
            save_results(SAMPLE_TALENTS[:TOP_N], degraded=True, reason=f"未捕获异常: {e}")
            write_state("done", degraded=True, reason="发生异常，已输出样例数据", result_file="result.json")
        except Exception:
            print(tb, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
