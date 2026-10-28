"""Case 注册表:按 --case 名称加载 cases/{case}/gateway.py 并抢占单例锁。

每个 case 目录下需要提供:
  - ``gateway.py`` 暴露 ``build_gateway(config, ark, sender, loop)`` 返回一个
    对象,该对象至少实现 ``accept(message)``,可选 ``on_card_action(action)``。
  - ``config.py`` 暴露 ``load_case_config(env=None)`` 返回该 case 特有的 dataclass。

Case 目录约定:``scenarios/feishu-bot/cases/{case}/``,case 名允许下划线或短横线。
"""
from __future__ import annotations

import errno
import fcntl
import importlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CASES_ROOT = Path(__file__).resolve().parent.parent / "cases"


@dataclass
class CaseModule:
    name: str
    directory: Path
    gateway_module: object
    config_module: object


def _case_names(*, exposed_only: bool) -> list[str]:
    if not CASES_ROOT.exists():
        return []
    names: list[str] = []
    for entry in sorted(CASES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        if not (entry / "gateway.py").exists() or not (entry / "config.py").exists():
            continue
        metadata_path = entry / "case.json"
        exposed = True
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"无法读取 case 元数据 {metadata_path}: {error}") from error
            exposed = bool(metadata.get("exposed", True))
        if not exposed_only or exposed:
            names.append(entry.name)
    return names


def list_cases() -> list[str]:
    """返回公开的合法 case 名(按字典序)。"""
    return _case_names(exposed_only=True)


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_case(name: str) -> CaseModule:
    """按名称加载 case 的 gateway/config 模块;不存在时给出可选项提示。"""
    available = _case_names(exposed_only=False)
    if name not in available:
        raise RuntimeError(
            f"未知 case:{name!r}。可选:{', '.join(available) if available else '(无)'}"
        )
    directory = CASES_ROOT / name
    safe_name = name.replace("-", "_")
    if (directory / "__init__.py").exists() and name.isidentifier():
        package = f"cases.{name}"
        config_module = importlib.import_module(f"{package}.config")
        gateway_module = importlib.import_module(f"{package}.gateway")
    else:
        # Legacy cases with non-package names (for example digital-employee) retain
        # their isolated, single-case loader until they are packaged separately.
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        config_module = _load_module(
            f"arkagent_case_{safe_name}_config", directory / "config.py"
        )
        sys.modules["config"] = config_module
        gateway_module = _load_module(
            f"arkagent_case_{safe_name}_gateway", directory / "gateway.py"
        )
    return CaseModule(
        name=name,
        directory=directory,
        gateway_module=gateway_module,
        config_module=config_module,
    )


class GatewayLock:
    """基于 fcntl.flock 的独占锁:整个 arkagent run 生命周期只能有一个。

    进入 with 时抢占;抢不到抛 RuntimeError 并显示锁文件路径,由 CLI 顶层转成
    友好错误。使用文件描述符保持锁,进程退出时内核会释放。
    """

    def __init__(self, lock_path: str) -> None:
        self._path = lock_path
        self._fd: Optional[int] = None

    def __enter__(self) -> "GatewayLock":
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(fd)
            if error.errno in (errno.EAGAIN, errno.EACCES):
                raise RuntimeError(
                    f"已有另一个 arkagent gateway 在运行(锁文件:{self._path})。"
                    "同一时刻只允许一个 gateway;请先停掉旧进程。"
                )
            raise
        # 写 pid 便于排查(不影响锁语义)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return self

    def __exit__(self, *_exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
