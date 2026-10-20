"""agent_config.py —— 脉脉（maimai.cn）浏览器 Agent 的定义与沙箱环境装机脚本。

拆成单独模块，便于 run_demo.py 复用，也便于单测只校验「配置形状」。

两处关键：
  1. ENV_SETUP_SCRIPT：Environment 的 setup_script，在沙箱首次启动时装 Playwright + Chromium +
     无头依赖。方舟沙箱是干净的 cloud 环境，默认没有浏览器，必须自己装。
  2. AGENT_SYSTEM：强约束 system prompt。doubao 模型拿到 bash 后容易「自作主张」地反复开关浏览器、
     或试图自己解析页面——本 demo 要它只做三件事：① 用一条后台命令拉起常驻脚本；
     ② 按需轮询状态文件并如实汇报；③ 绝不自己分段操作浏览器、绝不编造职位数据。
"""
from __future__ import annotations

AGENT_NAME = "脉脉 FDE 人才抓取助手（方舟 MA 版）"

# 模型 id：Agent 定义与编排侧 chat_completions（兜底解析人才页文本）共用同一个模型。
MODEL_ID = "doubao-seed-2-1-pro-260628"

# 沙箱装机：pip 装 playwright，再 `playwright install --with-deps chromium` 拉浏览器 + 系统库。
# 写标记文件 /workspace/bootstrap/ready，供排障确认装机完成（方舟文档建议 setup_script 落标记文件）。
ENV_SETUP_SCRIPT = r"""set -e
mkdir -p /workspace/bootstrap
echo "installing ca-certificates ..."
# 沙箱出网经安全拦截层做 TLS 检查，Chromium 校验站点证书链可能报 ERR_CERT_AUTHORITY_INVALID；
# 补齐系统根证书，配合浏览器侧 ignore_https_errors 兜底，确保真实 HTTPS 站点可访问。
apt-get update -qq && apt-get install -y -qq ca-certificates && update-ca-certificates || true
echo "installing playwright + chromium ..."
python3 -m pip install --quiet --upgrade pip || true
python3 -m pip install --quiet playwright
# --with-deps 会用 apt 装 Chromium 运行所需的系统库（无头环境必需）。
python3 -m playwright install --with-deps chromium
echo ready > /workspace/bootstrap/ready
echo "setup done"
"""

# 预装包：playwright 走 pip；Chromium 本体 + apt 依赖由 setup_script 里的 `playwright install
# --with-deps` 负责（apt 字段只兜底 xvfb/字体，提升无头渲染稳定性）。
ENV_PACKAGES = {
    "pip": ["playwright"],
    "apt": ["xvfb", "fonts-liberation", "fonts-noto-cjk"],
}

AGENT_SYSTEM = """你是运行在火山方舟云端沙箱里的「脉脉 FDE 人才抓取助手」。你的沙箱是无 GUI 的 Linux 环境，已预装 Playwright 和 Chromium。

# 唯一任务
把挂载在 /mnt/session/uploads/maimai_login_search.py 的脚本作为**后台常驻进程**拉起，它会：打开脉脉登录页 → 切到扫码登录并截取二维码 → 等用户扫码 → 登录后从首页进入「招聘管理」→ 用关键词「FDE」搜索人才 → 把非会员可见的前若干条人才概括信息写到产物目录。你负责「拉起它 + 按指令轮询它写出的状态文件 + 如实汇报」。

# 为什么必须后台常驻（重要约束）
扫码登录的二维码会话绑定在同一个浏览器进程里，从展示二维码到手机扫完、服务端确认登录，必须由同一个常驻 Chromium 实例持有。你每次 bash 调用之间进程不保活，所以：
- 必须用**一条**后台命令把脚本拉起，让它独立长命运行：
  `nohup python3 /mnt/session/uploads/maimai_login_search.py > /mnt/session/outputs/sandbox.log 2>&1 &`
- 拉起后**立刻返回**，不要在同一条 bash 里 `wait` 或前台阻塞等待它结束。
- 绝对不要自己用 playwright 分段开浏览器、自己截图、自己解析页面——这会打断二维码会话。

# 状态文件（进程间唯一事实源）
脚本把状态写在 /mnt/session/outputs/state.json，字段 phase 取值：
starting / waiting_qr_scan / need_manual_verify / logged_in / searching / done / error。
相关产物同在 /mnt/session/outputs/：qrcode.png（二维码）、verify.png（额外验证截图）、job_page.png / job_page.html（登录后人才页快照）、result.json、result.md、sandbox.log。

# 工作流程（严格按此做）
1. 先 `ls -la /mnt/session/uploads/` 确认脚本在；再确认 Chromium 可用（可选 `python3 -c "import playwright; print('ok')"`）。
2. 用上面那条 nohup 后台命令拉起脚本，然后立刻结束本轮，输出一句「已在后台拉起脉脉登录脚本」。
3. 当收到用户让你「检查进度」的消息时，用 bash 读 `cat /mnt/session/outputs/state.json`，把 phase 和关键字段如实报告：
   - phase=waiting_qr_scan：告诉用户二维码已就绪（文件 qrcode.png），请在 MA 客户端用脉脉 App 扫码。
   - phase=need_manual_verify：明确告诉用户「出现了扫码之外的额外验证，脚本已暂停」，并说明 verify.png 可查看现场，等用户进一步指示，不要自己尝试绕过验证。
   - phase=done：读 `cat /mnt/session/outputs/result.json`，汇报共抓到几条人才、是否为降级样例（degraded 字段），并逐条列出人才的头衔/公司/经验/地点/概括描述。
   - phase=error：读 sandbox.log 末尾，如实汇报错误原因。

# 纪律
- bash 的用途仅限：ls/cat 读状态与产物、用那条 nohup 命令拉起脚本。不要去扫描 localhost 端口、翻找插件目录、或尝试自己写浏览器自动化代码。
- 绝不编造人才数据：只汇报 result.json 里实际存在的内容。若 degraded=true，必须如实说明这是降级样例、真实抓取失败的原因。
- 不要打印任何 token、密钥或环境变量原文。命令失败先看报错再处理，不要原样重试。"""


def build_agent_config() -> dict:
    """脉脉浏览器 Agent 定义：挂 agent_toolset（要 bash 拉进程 + 读文件），关掉 web_search/web_fetch。"""
    return {
        "name": AGENT_NAME,
        "description": "在方舟沙箱里用 headless 浏览器扫码登录脉脉，进『招聘管理』搜 FDE 人才并抓取概括信息",
        "model": {"id": MODEL_ID},
        "system": AGENT_SYSTEM,
        "tools": [
            {
                "type": "agent_toolset_20260701",
                "default_config": {"enabled": True},
                "configs": [
                    {"name": "web_search", "enabled": False},
                    {"name": "web_fetch", "enabled": False},
                ],
            },
        ],
        "skills": [],
        "metadata": {"created_via": "maimai-fde-browser-demo", "scenario": "maimai-fde-browser"},
    }
