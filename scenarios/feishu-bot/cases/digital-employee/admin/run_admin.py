"""数字员工可视化管理系统 —— 启动入口。

构造 ``ArkClient`` + ``ConfigStore`` + ``MAControlPlane``，注入飞书 lark-cli 置备器
（env/vault，供同步时按需置备），起 uvicorn 托管 REST API + 静态前端。

**仅限本地/内网使用**，未做完整鉴权体系；设 ``ADMIN_API_TOKEN`` 后 ``/api/*`` 需带
``Authorization: Bearer <token>``。切勿直接暴露公网。

运行：
  set -a && source ~/.arkagent/config.env && set +a   # 需要 ARK_API_KEY[/ARK_BASE_URL]
  python scenarios/feishu-bot/cases/digital-employee/admin/run_admin.py

可选环境变量：
  ADMIN_HOST            默认 127.0.0.1
  ADMIN_PORT            默认 8787
  ADMIN_API_TOKEN       非空则开启 token 鉴权
  ARK_BASE_URL          默认 https://ark.cn-beijing.volces.com/api/v3
  DIGITAL_EMPLOYEE_ADMIN_DB_PATH   配置库路径，默认 data/digital_employee_admin.db
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 对齐 case 目录扁平 import 约定：把 case 目录（含 shared.py）与本 admin 目录都加入 sys.path，
# 使 `python .../admin/run_admin.py` 直接启动时也能 import 到 shared / server。
_ADMIN_DIR = Path(__file__).resolve().parent
_CASE_DIR = _ADMIN_DIR.parent
for _p in (str(_CASE_DIR), str(_ADMIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uvicorn

from arkagent.ark import ArkClient
from arkagent.gateway import ConfigStore, MAControlPlane

from server import create_app

# 复用 case 目录里已实现的 lark-cli 幂等置备器，作为控制面的 provisioner 注入，
# 避免 gateway 包反向依赖 case 目录（依赖注入方向：case → gateway）。
try:
    from shared import ensure_lark_cli_environment, ensure_lark_cli_vault
except ImportError:  # pragma: no cover - 缺省时不影响纯 CRUD，仅同步置备不可用
    ensure_lark_cli_environment = None  # type: ignore[assignment]
    ensure_lark_cli_vault = None  # type: ignore[assignment]


def build_app():
    api_key = (os.environ.get("ARK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 ARK_API_KEY。请先 export 或 source 主包 config.env。")
    base_url = (
        os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
    ).rstrip("/")
    token = (os.environ.get("ADMIN_API_TOKEN") or "").strip()

    ark = ArkClient(api_key, base_url)
    store = ConfigStore()
    plane = MAControlPlane(
        ark,
        store,
        env_provisioner=ensure_lark_cli_environment,
        vault_provisioner=ensure_lark_cli_vault,
    )
    return create_app(store=store, plane=plane, token=token)


def main() -> None:
    host = os.environ.get("ADMIN_HOST") or "127.0.0.1"
    port = int(os.environ.get("ADMIN_PORT") or "8787")
    app = build_app()
    if os.environ.get("ADMIN_API_TOKEN", "").strip():
        print(f"[admin] token 鉴权已开启，/api/* 需带 Authorization: Bearer <token>")
    else:
        print("[admin] 未设 ADMIN_API_TOKEN：/api/* 无鉴权，仅限本地/内网使用，勿暴露公网。")
    print(f"[admin] 监听 http://{host}:{port}  （Ctrl-C 退出）")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
