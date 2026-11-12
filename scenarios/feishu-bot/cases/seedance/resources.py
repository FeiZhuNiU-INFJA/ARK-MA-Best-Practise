"""统一 MA 资源装配入口：创建／原地更新，ID 安全回写 case 配置。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from arkagent.ark import ArkError
from arkagent.config import DEFAULT_ARK_BASE_URL, parse_env_text, serialize_env
from arkagent.paths import get_case_paths

from .config import required

RESOURCE_DIR = Path(__file__).resolve().parent / "ma-resources"
BASE_AGENT = "ultra_vision_agent_preview"


def render(value, variables: dict[str, str]):
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    if isinstance(value, str):
        return re.sub(r"\$\{([A-Z_]+)\}", lambda match: required(variables, match.group(1)), value)
    return value


def build_payloads(values: dict[str, str]) -> tuple[dict, dict]:
    variables = {
        **values,
        "SEEDANCE_SYSTEM_PROMPT": (RESOURCE_DIR / "system.md").read_text(encoding="utf-8"),
    }
    agent = render(json.loads((RESOURCE_DIR / "agent.json").read_text(encoding="utf-8")), variables)
    environment = render(json.loads((RESOURCE_DIR / "environment.json").read_text(encoding="utf-8")), variables)
    if agent.get("base_agent") != BASE_AGENT or agent.get("model", {}).get("reasoning_effort") != "high":
        raise ValueError("Seedance 必须配置 ultra_vision_agent_preview 和 high 推理强度")
    tools = {tool["type"]: tool for tool in agent["tools"]}
    base = tools.get("agent_toolset_20260916", {})
    multimodal = tools.get("agent_toolset_multimodal_20260825", {})
    configs = {item["name"]: item for item in multimodal.get("configs", [])}
    for name in ("image_generate", "video_generate", "visual_search", "general_search", "compose_video"):
        if not configs.get(name, {}).get("enabled"):
            raise ValueError(f"必须启用视觉工具：{name}")
    if not any(item.get("name") == "web_search" and item.get("enabled") is False for item in base.get("configs", [])):
        raise ValueError("启用 general_search 时必须关闭基础工具集 web_search")
    for name in ("image_generate", "video_generate"):
        models = configs[name].get("models", [])
        if not 1 <= len(models) <= 10 or sum(item.get("default") is True for item in models) != 1:
            raise ValueError(f"{name} 必须配置 1—10 个模型及唯一默认模型")
        if not all(item.get("id") for item in models):
            raise ValueError(f"{name} 缺少模型 ID")
    return agent, environment


def save_ids(path: Path, updates: dict[str, str]) -> None:
    """原子合并并写入；文件 0600，不 source 执行配置内容。"""
    current = parse_env_text(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".seedance-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialize_env(current))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ResourceClient:
    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=60,
        )

    async def request(self, method: str, path: str, body=None) -> dict:
        response = await self.client.request(method, self.base_url + path, json=body)
        if response.status_code >= 400:
            # 保留错误码和 request_id，不输出可能包含凭据的请求／响应正文。
            try:
                code = response.json().get("error", {}).get("code", "")
            except (ValueError, AttributeError):
                code = ""
            raise ArkError(
                f"MA {method} {path.split('?')[0]} 失败：HTTP {response.status_code} "
                f"{code}，request_id={response.headers.get('x-request-id', '')}",
                status_code=response.status_code,
            )
        payload = response.json() if response.content else {}
        return payload if isinstance(payload, dict) else {"data": payload}

    async def find(self, collection: str, name: str) -> str:
        page = ""
        seen: set[str] = set()
        matches: set[str] = set()
        while True:
            query = {"limit": "100"}
            if page:
                query["page"] = page
            payload = await self.request("GET", f"/{collection}?{urlencode(query)}")
            data = payload.get("data", [])
            if isinstance(data, dict):
                data = data.get("items", [])
            for item in data:
                if item.get("name") == name and item.get("id"):
                    matches.add(str(item["id"]))
            page = str(payload.get("next_page") or "")
            if not page:
                break
            if page in seen:
                raise RuntimeError("MA 资源列表分页未推进")
            seen.add(page)
        if len(matches) > 1:
            raise RuntimeError(f"存在多个同名 {collection}，请在 case 配置中明确指定资源 ID")
        return next(iter(matches), "")


def unwrap(payload: dict) -> dict:
    return payload["data"] if isinstance(payload.get("data"), dict) else payload


def optional_id(values: dict[str, str], key: str) -> str:
    value = (values.get(key) or "").strip()
    return "" if "${" in value else value


async def provision(client: ResourceClient, values: dict[str, str], config_file: Path) -> dict[str, str]:
    agent, environment = build_payloads(values)
    agent_id = optional_id(values, "SEEDANCE_AGENT_ID")
    agent_id = agent_id or await client.find("agents", agent["name"])
    current = None
    if agent_id:
        current = unwrap(await client.request("GET", f"/agents/{quote(agent_id, safe='')}"))
        if current.get("base_agent") != BASE_AGENT:
            raise RuntimeError("已配置的 Agent 不是 Seedance 类型，无法修改 base_agent；请改用 Seedance Agent ID")
        if current.get("version") is None:
            raise RuntimeError("Agent 响应缺少 version，无法原地更新")
    # 每个资源成功后立即记录 ID；后续失败时下次运行可继续，创建请求不自动重试。
    env_id = optional_id(values, "SEEDANCE_ENVIRONMENT_ID")
    env_id = env_id or await client.find("environments", environment["name"])
    if env_id:
        await client.request("GET", f"/environments/{quote(env_id, safe='')}")
        await client.request("POST", f"/environments/{quote(env_id, safe='')}", {"config": environment["config"]})
    else:
        result = unwrap(await client.request("POST", "/environments", environment))
        env_id = str(result.get("id") or "")
        if not env_id:
            raise RuntimeError("创建 Environment 未返回 ID")
    save_ids(config_file, {"SEEDANCE_ENVIRONMENT_ID": env_id})

    if agent_id:
        # base_agent 创建后不可修改，更新请求不携带此字段。
        update = {key: value for key, value in agent.items() if key != "base_agent"}
        update["version"] = int(current["version"])
        await client.request("POST", f"/agents/{quote(agent_id, safe='')}", update)
    else:
        result = unwrap(await client.request("POST", "/agents", agent))
        agent_id = str(result.get("id") or "")
        if not agent_id:
            raise RuntimeError("创建 Agent 未返回 ID")
    updates = {"SEEDANCE_AGENT_ID": agent_id, "SEEDANCE_ENVIRONMENT_ID": env_id}
    save_ids(config_file, updates)
    return updates


async def run(args) -> None:
    path = Path(args.config).expanduser().resolve()
    values = dict(os.environ)
    if path.exists():
        values.update(parse_env_text(path.read_text(encoding="utf-8")))
    if args.dry_run:
        agent, environment = build_payloads(values)
        # Environment 可含部署者自行配置的 env / TOS；不打印这些配置。
        print(json.dumps({"agent": agent, "environment": {"name": environment["name"], "type": environment["config"]["type"]}}, ensure_ascii=False, indent=2))
        return
    if not path.exists():
        raise RuntimeError(f"配置文件不存在：{path}；先创建 case 配置或运行 arkagent init --case seedance")
    client = ResourceClient(required(values, "ARK_API_KEY"), values.get("ARK_BASE_URL") or DEFAULT_ARK_BASE_URL)
    try:
        if args.check:
            agent_id = required(values, "SEEDANCE_AGENT_ID")
            current = unwrap(await client.request("GET", f"/agents/{quote(agent_id, safe='')}"))
            if current.get("base_agent") != BASE_AGENT:
                raise RuntimeError("Agent 不是 Seedance 类型")
            env_id = required(values, "SEEDANCE_ENVIRONMENT_ID")
            await client.request("GET", f"/environments/{quote(env_id, safe='')}")
            print("Seedance Agent 与 Environment 可访问；飞书连接由 arkagent run 验证。")
            return
        updates = await provision(client, values, path)
        for key, value in updates.items():
            print(f"{key}={value}")
        print(f"资源已创建／原地更新，ID 写入 {path}。")
        print("运行 arkagent run --case seedance；已运行的 Gateway 请重启，旧创作使用 /new 切到新配置。")
    finally:
        await client.client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("SEEDANCE_CONFIG_FILE") or get_case_paths("seedance").config_path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅渲染模型和工具配置，不调用远端接口")
    mode.add_argument("--check", action="store_true", help="只读检查已配置的 Agent 和 Environment")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (RuntimeError, ValueError, httpx.HTTPError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
