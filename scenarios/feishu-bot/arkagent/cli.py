"""arkagent 命令行入口：init / doctor / run。

架构：飞书 WS 客户端在主线程阻塞运行；asyncio 事件循环跑在后台线程，承载
Gateway 的异步编排与方舟调用。WS 回调（主线程）通过 loop.call_soon_threadsafe
把处理协程投递到事件循环，满足飞书 3 秒处理约束。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading

from .config import load_config, load_config_file
from .masked_input import read_masked_input
from .paths import get_arkagent_paths


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    command = argv[0] if argv else "run"
    try:
        if command == "run":
            _run(argv[1:])
        elif command == "doctor":
            asyncio.run(_doctor())
        elif command == "init":
            if "--topic6" in argv[1:]:
                asyncio.run(_init_topic6())
            else:
                asyncio.run(_init())
        elif command == "update-agent":
            asyncio.run(_update_agent(argv[1:]))
        else:
            _print_help()
        return 0
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as error:  # noqa: BLE001 - 顶层兜底，给出友好提示
        print(str(error), file=sys.stderr)
        if str(error).startswith("缺少环境变量"):
            print("请运行 arkagent init 完成交互式配置。", file=sys.stderr)
        return 1


def _load_saved_environment():
    paths = get_arkagent_paths()
    if os.path.exists(paths.config_path):
        load_config_file(paths.config_path)
    return paths


def _mask_identity(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:5]}***{value[-3:]}"


def _build_managers(config, ark, store):
    from .memory import MemoryManager
    from .role import RoleManager, mock_hr_provider

    role_manager = RoleManager(store, mock_hr_provider, ttl_ms=config.role_ttl_ms)
    memory_manager = MemoryManager(ark, store, team_store_enabled=config.team_store_enabled)
    return role_manager, memory_manager


def _run(args: list[str] | None = None) -> None:
    """按 --case <name> 分派到对应的 cases/{case}/gateway.py。

    没传 --case 就列出可选项并退出。同一飞书 App(FEISHU_APP_ID)只允许一个 gateway 进程,
    锁路径为 ~/.arkagent/feishu.{app_id}.lock;不同 Bot 的 case 可并行运行。
    每个 case 的环境变量走 ~/.arkagent/cases/{case}/config.env。
    """
    from .ark import ArkClient
    from .case_registry import GatewayLock, list_cases, load_case
    from .config import load_config_file
    from .feishu import FeishuSender, start_feishu_gateway
    from .paths import get_case_paths, get_feishu_lock_path

    logging.basicConfig(
        level=os.environ.get("ARKAGENT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    case_name = _parse_case_arg(args or [])
    available = list_cases()
    if not case_name:
        _print_case_list(available)
        raise RuntimeError("请通过 --case <name> 指定要运行的场景")

    case = load_case(case_name)
    case_paths = get_case_paths(case_name)
    os.makedirs(case_paths.case_dir, exist_ok=True)

    if os.path.exists(case_paths.config_path):
        load_config_file(case_paths.config_path)
    config = case.config_module.load_case_config()

    # 锁按 FEISHU_APP_ID 分粒度:真正的互斥资源是「同一 Bot 的 WS 长连接」,
    # 不同 case 使用不同 Bot 时可以并行运行。
    lock_path = get_feishu_lock_path(config.feishu_app_id)
    with GatewayLock(lock_path):
        ark = ArkClient(config.ark_api_key, config.ark_base_url)
        sender = FeishuSender(config.feishu_app_id, config.feishu_app_secret)

        loop = asyncio.new_event_loop()
        gateway = case.gateway_module.build_gateway(config, ark, sender, loop)

        thread = threading.Thread(target=loop.run_forever, name="gateway-loop", daemon=True)
        thread.start()

        print(f"Gateway 配置(case={case_name}):")
        print(f"- 飞书 App ID:{config.feishu_app_id}")
        print(f"- 环境变量文件:{case_paths.config_path}")
        print(f"- 锁文件:{lock_path}(pid={os.getpid()};按 App ID 分粒度)")
        print("正在连接飞书 WebSocket;请在该 Bot 会话中发送消息。")
        start_feishu_gateway(config.feishu_app_id, config.feishu_app_secret, gateway)


def _parse_case_arg(args: list[str]) -> str | None:
    for i, arg in enumerate(args):
        if arg == "--case":
            if i + 1 >= len(args):
                raise RuntimeError("--case 需要一个 case 名参数")
            return args[i + 1].strip() or None
        if arg.startswith("--case="):
            return arg.split("=", 1)[1].strip() or None
    return None


def _print_case_list(available: list[str]) -> None:
    if available:
        print("可选 case:")
        for name in available:
            print(f"  - {name}")
        print("用法:arkagent run --case <name>")
    else:
        print("未发现任何 case(cases/ 下需包含 gateway.py 与 config.py)")


async def _doctor() -> None:
    from .ark import ArkClient

    _load_saved_environment()
    config = load_config()
    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    try:
        agent = await ark.get_agent(config.ark_agent_id)
        version = f" v{agent['version']}" if agent.get("version") else ""
        print(f"配置有效；已连接 Agent {agent['id']}{version}。")
        print(f"MCP Server：{config.mcp_server_url}")
        print("飞书连接将在 run 命令启动时由 lark-oapi 完成鉴权。")
    finally:
        await ark.aclose()


async def _init() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError("交互式 init 需要在终端中运行")

    from .ark import ArkClient
    from .init import run_guided_init
    from .node_helper import register_feishu_app

    paths = get_arkagent_paths()

    ark_api_key = read_masked_input("火山方舟 API Key（输入内容以 • 显示）: ").strip()
    if not ark_api_key:
        raise RuntimeError("方舟 API Key 不能为空")
    mcp_server_url = input("mock 客户A MCP 公网地址（形如 https://xxx/mcp）: ").strip()
    if not mcp_server_url:
        raise RuntimeError("MCP Server 地址不能为空")
    mcp_static_bearer = read_masked_input("mock MCP 的 static Bearer token（输入内容以 • 显示）: ").strip()
    if not mcp_static_bearer:
        raise RuntimeError("MCP static Bearer token 不能为空")

    async def create_feishu_app():
        # registerApp 无 Python 等价物，走 Node 子进程扫码建应用。
        return await asyncio.get_event_loop().run_in_executor(None, register_feishu_app)

    result = await run_guided_init(
        ark_api_key=ark_api_key,
        mcp_server_url=mcp_server_url,
        mcp_static_bearer=mcp_static_bearer,
        create_ark=lambda key, base: ArkClient(key, base),
        create_feishu_app=create_feishu_app,
        env_path=paths.config_path,
        gateway_database_path=paths.database_path,
    )
    print(f"已创建客户A销售助手 Agent：{result.agent_id}")
    print(f"{'已创建' if result.environment_created else '已复用'} Environment：{result.environment_id}")
    print(f"已配置 static_bearer 凭据：{result.credential_id}（创建时已握手探测 MCP）")
    print(f"配置已安全写入 {result.env_path}。")
    print("初始化完成。运行 `arkagent run` 启动 Gateway。")


async def _init_topic6() -> None:
    """topic6 专用轻量初始化:只建飞书应用 + 写 case 私有 config.env。

    与默认 ``init`` 的区别:不创建 digital-employee Agent、不要求 mock 客户A MCP 公网地址。
    topic6 的 MA 资源(Environment/Memory/Coordinator 等)由
    ``cases/topic6/ma-resources/create_all.sh`` 单独创建,产出的 ID 再追加到 case 的 config.env。
    """
    if not sys.stdin.isatty():
        raise RuntimeError("交互式 init 需要在终端中运行")

    from .config import parse_env_text, serialize_env
    from .init import _write_secure
    from .node_helper import register_feishu_app
    from .paths import get_case_paths

    case_paths = get_case_paths("topic6")

    ark_api_key = read_masked_input("火山方舟 API Key(输入内容以 • 显示): ").strip()
    if not ark_api_key:
        raise RuntimeError("方舟 API Key 不能为空")

    print("接下来扫码创建飞书应用(topic6 将复用该 Bot)…")
    feishu_app = await asyncio.get_event_loop().run_in_executor(None, register_feishu_app)

    existing: dict[str, str] = {}
    if os.path.exists(case_paths.config_path):
        with open(case_paths.config_path, "r", encoding="utf-8") as fh:
            existing = parse_env_text(fh.read())
    existing.update(
        {
            "ARK_API_KEY": ark_api_key,
            "FEISHU_APP_ID": feishu_app.app_id,
            "FEISHU_APP_SECRET": feishu_app.app_secret,
        }
    )
    _write_secure(case_paths.config_path, serialize_env(existing))

    print(f"飞书 Bot 已创建:{feishu_app.app_id}")
    print(f"配置已写入 {case_paths.config_path}(仅含方舟 Key + 飞书凭据)。")
    print("下一步:")
    print("  1) cd cases/topic6 && ./tools/pack_skills.sh && python3 tools/upload_skills.py")
    print("  2) ./ma-resources/create_all.sh")
    print(f"  3) 把输出的 TOPIC6_* ID 追加到 {case_paths.config_path},然后 `arkagent run --case topic6`")


async def _update_agent(args: list[str] | None = None) -> None:
    from .ark import ArkClient
    from .config import update_env_file
    from .init import CREDENTIAL_NAME, build_customer_a_agent_config

    new_mcp_url = _parse_mcp_url_arg(args or [])

    paths = _load_saved_environment()
    config = load_config()
    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    try:
        current = await ark.get_agent(config.ark_agent_id)
        if current.get("version") is None:
            raise RuntimeError(f"无法获取 Agent {config.ark_agent_id} 的当前版本，无法更新")
        version = int(current["version"])

        # 换 MCP 地址：static_bearer 凭据的 mcp_server_url 创建后锁定，只能删旧建新。
        # 安全顺序——先用新地址建凭据（方舟会握手探测，不可达即 4xx 中止，旧配置无损），
        # 再更新 Agent 指向新地址，写回 .env，最后删旧凭据。
        target_mcp_url = config.mcp_server_url
        if new_mcp_url and new_mcp_url != config.mcp_server_url:
            if _looks_like_placeholder_token(config.mcp_static_bearer):
                raise RuntimeError(
                    "换 MCP 地址需要有效的 static_bearer token，但 config.env 中的 MCP_STATIC_BEARER "
                    "缺失或疑似占位/误填（为空、形如 'xxx' 或被填成了 URL）。请在 config.env 补上 mock "
                    "实际校验的 token（与启动 mock 时的 MCP_STATIC_BEARER 一致），或重新运行 arkagent init。"
                )
            target_mcp_url = new_mcp_url
            # 清理所有 URL 与新地址不一致的旧 static_bearer 凭据——不按名字匹配，
            # 以兼容历史遗留的异名凭据（如脱敏前的 nio-mcp-static-bearer），避免留下
            # 指向旧地址的残留导致方舟找不到匹配凭据、退化成匿名连接（auth 缺失 → 401）。
            old_credentials = [
                c for c in await ark.list_credentials(config.ark_vault_id)
                if c["auth_type"] == "static_bearer" and c.get("mcp_server_url") != target_mcp_url
            ]
            new_credential_id = await ark.create_static_bearer_credential(
                config.ark_vault_id, CREDENTIAL_NAME, target_mcp_url, config.mcp_static_bearer
            )
            print(f"已用新地址创建 static_bearer 凭据：{new_credential_id}（创建时已握手探测 MCP 可达）")

        new_config = build_customer_a_agent_config(target_mcp_url)
        updated = await ark.update_agent(config.ark_agent_id, new_config, version)

        if target_mcp_url != config.mcp_server_url:
            update_env_file(paths.config_path, {"MCP_SERVER_URL": target_mcp_url})
            # Agent 已切到新凭据，旧凭据（指向旧地址）可安全删除。
            for old in old_credentials:
                await ark.delete_credential(config.ark_vault_id, old["id"])
                print(f"已删除指向旧地址的凭据：{old['id']}")

        print(f"已更新 Agent {updated['id']}：v{version} → v{updated['version']}")
        print(f"MCP Server：{target_mcp_url}")
        print("Agent ID 不变，飞书 bot 无需重建。运行 `arkagent run` 即用新配置。")
    finally:
        await ark.aclose()


def _looks_like_placeholder_token(token: str | None) -> bool:
    """判断 static_bearer token 是否缺失/疑似占位或误填，用于换址前早失败。

    命中任一即视为无效：为空、纯占位（如 xxx/changeme/placeholder/todo）、
    或被误填成了 URL（http/https 开头）——这些都无法通过方舟对 MCP 的握手鉴权探测。
    """
    value = (token or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return True
    if set(lowered) <= {"x"}:  # 全是 x，如 "xxx"
        return True
    return lowered in {"changeme", "placeholder", "todo", "your-token", "token"}


def _parse_mcp_url_arg(args: list[str]) -> str | None:
    """从 update-agent 的参数里解析 --mcp-url <地址> / --mcp-url=<地址>。未提供返回 None。"""
    for index, token in enumerate(args):
        if token == "--mcp-url":
            if index + 1 >= len(args):
                raise RuntimeError("--mcp-url 需要一个地址参数，例如 --mcp-url https://xxx/mcp")
            return args[index + 1].strip()
        if token.startswith("--mcp-url="):
            return token[len("--mcp-url="):].strip()
    return None


def _print_help() -> None:
    print(
        "arkagent [command]\n\n"
        "  init          交互式创建 客户A 销售助手 Agent + 飞书应用（扫码）+ static_bearer 凭据\n"
        "  init --topic6 仅扫码创建飞书 Bot + 写最小配置(不建客户A Agent、不需要 MCP 地址)\n"
        "  update-agent  用最新的 system prompt/工具配置更新现有 Agent（不重扫码、不新建 bot）\n"
        "                加 --mcp-url <新地址> 可一并换 MCP 公网地址：重建 static_bearer 凭据 + 写回 config.env\n"
        "  doctor        检查配置并验证方舟 Agent\n"
        "  run           启动本地 Gateway（默认）"
    )


if __name__ == "__main__":
    sys.exit(main())
