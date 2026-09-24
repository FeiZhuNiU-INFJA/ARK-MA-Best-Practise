#!/usr/bin/env python3
"""共用底座：API 客户端、批量并发、断点续跑、成本台账、截断显式报错。

所有阶段脚本都走这里，不要各自造轮子。
凭据只从环境变量读取。鉴权按以下顺序取第一个非空值：
  BASE_URL：ANTHROPIC_BASE_URL（指向 bmc 中转网关）
  Key：ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN
Embedding 另需：EMBEDDING_BASE_URL / EMBEDDING_API_KEY（缺省沿用上面两个）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

# 每百万 token 单价，仅用于成本台账的估算。换模型时在这里补。
PRICING = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "text-embedding-3-small": (0.02, 0.0),
    "Doubao-embedding": (0.0, 0.0),
}

# Bedrock 后端对这些模型报 "`temperature` is deprecated for this model"，
# 带上该字段会 400。这些模型只能跑默认温度。
NO_TEMPERATURE = ("claude-opus-4-7",)


def price_of(model: str) -> tuple[float, float]:
    for key, value in PRICING.items():
        if model.startswith(key) or key in model:
            return value
    return (0.0, 0.0)


class Relay:
    """Anthropic messages 接口客户端，线程安全累计用量。"""

    def __init__(self, model: str, max_tokens: int, system: str,
                 temperature: float = 0.0, timeout: int = 600, retries: int = 3):
        # Claude Code 环境通常只导出 ANTHROPIC_AUTH_TOKEN（指向 bmc 中转网关），
        # 不导出 ANTHROPIC_API_KEY。两个都认，省掉每条命令手动加前缀。
        self.base = (os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self.key = (os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN") or "")
        if not self.base or not self.key:
            raise SystemExit(
                "缺少凭据。需要 ANTHROPIC_BASE_URL，以及 ANTHROPIC_API_KEY 或 "
                "ANTHROPIC_AUTH_TOKEN 之一。凭据只从环境变量读取，不要写进文件。")
        self.model = model
        self.max_tokens = max_tokens
        self.system = system
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries
        self.lock = threading.Lock()
        self.usage = {"input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.calls = 0

    def call(self, user_text: str, extra: str = "") -> str:
        cap = self.max_tokens
        payload_body = {
            "model": self.model,
            "max_tokens": cap,
            "system": [{"type": "text", "text": self.system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_text + extra}],
        }
        if not any(k in self.model for k in NO_TEMPERATURE):
            payload_body["temperature"] = self.temperature
        body = json.dumps(payload_body, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self.base + "/v1/messages", data=body,
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        last = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.load(resp)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                if attempt == self.retries - 1:
                    raise
                time.sleep(2 ** attempt)
        with self.lock:
            self.calls += 1
            for key in self.usage:
                self.usage[key] += payload.get("usage", {}).get(key, 0) or 0
        # 截断必须显式报错。当成解析失败去重试，只会再截断一次。
        if payload.get("stop_reason") == "max_tokens":
            raise ValueError(f"输出被 max_tokens={cap} 截断，"
                             f"需调大 max_tokens 或减小批量")
        return "".join(b.get("text", "") for b in payload.get("content", []))

    def cost(self) -> float:
        pin, pout = price_of(self.model)
        return (self.usage["input_tokens"] * pin
                + self.usage["output_tokens"] * pout) / 1_000_000

    def call_json(self, user_text: str, attempts: int = 5) -> dict:
        """调用并解析。空返回或解析失败会重试——串行阶段一次失败会毁掉整轮。

        attempts 曾是 3、退避 1/2s，总重试窗口只有 3 秒。实测「网关返回空内容」
        是并发下的瞬时故障（同一个块单独跑 4/4 成功、8 路并发 7/8 成功，输出稳定
        在 7.5K 字符、离 max_tokens 差 8 倍），3 秒窗口盖不住一次网关抖动，
        于是被误判成「块内容有问题」而去拆块——拆块对这类失败无效且会切散事件。
        改成 5 次、退避 2/4/8/16s（窗口 30 秒）后，瞬时故障自己就恢复了。
        """
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                text = self.call(user_text)
                if not text.strip():
                    raise ValueError("模型返回空内容")
                return parse_json(text)
            except (ValueError, json.JSONDecodeError) as exc:
                if isinstance(exc, ValueError) and "max_tokens" in str(exc):
                    # 截断不在这里重试——它和「空返回」需要不同的处置，交给调用方决定。
                    # 07 的做法是：原样重试一次，仍截断才在块内对半拆。
                    raise
                last = exc
                if attempt < attempts - 1:
                    time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"连续 {attempts} 次无法取得可解析输出：{last!r}")


class Embedder:
    """向量化客户端，带磁盘缓存。缓存键是文本哈希 + 模型名。

    timeout 曾是 120s。实测该端点吞吐波动极大（同样参数在不同时段实测
    0.21 / 0.73 / 2.3 条每秒），单请求耗时随之从几十秒到 20 分钟以上。
    对照实验：955 条真实标题，timeout=120 在 1232s 后超时中断（只取到 832 条），
    timeout=600 完整跑完 955 条。调 batch 或并发都无效，只能放宽等待。
    """

    def __init__(self, model: str = "Doubao-embedding",
                 cache_path: Path | None = None, timeout: int = 600,
                 retries: int = 4):
        self.base = (os.environ.get("EMBEDDING_BASE_URL")
                     or os.environ.get("ANTHROPIC_BASE_URL", "")).rstrip("/")
        self.key = (os.environ.get("EMBEDDING_API_KEY")
                    or os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
        if not self.base or not self.key:
            raise SystemExit("缺少 embedding 凭据。需要 EMBEDDING_BASE_URL / "
                             "EMBEDDING_API_KEY，或沿用 ANTHROPIC_BASE_URL 加 "
                             "ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 之一。")
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.cache_path = cache_path
        self.cache: dict[str, list[float]] = {}
        self.hits = 0
        self.misses = 0
        self.usage = {"input_tokens": 0}
        if cache_path and cache_path.exists():
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
            if blob.get("model") == model:
                self.cache = blob.get("vectors", {})

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def embed(self, texts: Sequence[str], batch: int = 64,
              concurrency: int = 1) -> list[list[float]]:
        """吞吐主要由**模型选择**决定，不由 batch 或 concurrency 决定。

        实测（各 128 条全新文本）：Doubao-embedding 50.8 条/秒，
        text-embedding-3-small 1.01~2.53 条/秒——差 20~50 倍，因为后者要出海。
        而同一个模型换 batch 16/64、并发 16/32，四种组合全在 0.21~0.26 条/秒,
        同样参数在不同时段还能差 70 倍。所以**不要指望调这两个参数提速**，
        concurrency 只用来避免「全串行」这个最坏情况。
        """
        pending = [t for t in texts if self._key(t) not in self.cache]
        unique = list(dict.fromkeys(pending))
        chunks = [unique[i:i + batch] for i in range(0, len(unique), batch)]
        lock = threading.Lock()
        done = [0]

        def fetch(chunk: list[str]) -> None:
            body = json.dumps({"model": self.model, "input": chunk},
                              ensure_ascii=False).encode()
            req = urllib.request.Request(
                self.base + "/v1/embeddings", data=body,
                headers={"Authorization": f"Bearer {self.key}",
                         "content-type": "application/json"})
            for attempt in range(self.retries):
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        payload = json.load(resp)
                    break
                except (urllib.error.URLError, TimeoutError, OSError,
                        json.JSONDecodeError):
                    if attempt == self.retries - 1:
                        raise
                    time.sleep(2 ** attempt)
            with lock:
                self.usage["input_tokens"] += (
                    payload.get("usage", {}).get("prompt_tokens", 0) or 0)
                for text, item in zip(chunk, payload.get("data", [])):
                    self.cache[self._key(text)] = item["embedding"]
                self.misses += len(chunk)
                done[0] += 1
                if done[0] % 20 == 0:
                    # 分批落盘：整轮跑完才存的话，一次超时会丢掉全部已取向量
                    self.save()
                    print(f"[embed] {done[0]}/{len(chunks)} 批", flush=True)

        if concurrency > 1 and len(chunks) > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(fetch, c) for c in chunks]
                try:
                    for fut in as_completed(futures):
                        fut.result()
                except Exception:
                    self.save()  # 已取到的先落盘，重跑能续上
                    raise
        else:
            try:
                for chunk in chunks:
                    fetch(chunk)
            except Exception:
                self.save()
                raise
        out = []
        for text in texts:
            key = self._key(text)
            if key in self.cache and text not in pending:
                self.hits += 1
            out.append(self.cache[key])
        return out

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(
            {"model": self.model, "count": len(self.cache), "vectors": self.cache},
            ensure_ascii=False), encoding="utf-8")

    def cost(self) -> float:
        pin, _ = price_of(self.model)
        return self.usage["input_tokens"] * pin / 1_000_000


def repair_unescaped_quotes(text: str) -> str:
    """转义字符串值内部的裸 ASCII 双引号。

    实测模型会写出 "underlying_event":"官方辟谣"许昌暴雨"为谣言" 这种输出。
    判据：串内遇到 " 时向后跳过空白，若下一个字符不是 , : } ] 或结尾，它就是内容而非收尾。
    """
    out = []
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = in_str
            continue
        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
                continue
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j >= len(text) or text[j] in ",:}]":
                in_str = False
                out.append(ch)
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def parse_json(text: str) -> dict:
    """模型有时会加 markdown 围栏或前后缀说明，都剥掉。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    for candidate in (text, None):
        if candidate is None:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                break
            candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(repair_unescaped_quotes(candidate))
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("无法解析模型输出", text, 0)


def make_batches(items: Sequence[dict], size: int, id_field: str = "record_id",
                 tag: str = "B") -> list[dict]:
    """顺序切批。批大小一旦确定不要中途改——改了 batch_id 编号会错位，续跑对不上。"""
    out = []
    for i in range(0, len(items), size):
        chunk = items[i:i + size]
        out.append({"batch_id": f"{tag}{i // size:04d}",
                    "ids": [str(r[id_field]) for r in chunk],
                    "items": chunk})
    return out


def _batch_input_hash(batch: dict) -> str:
    """Ada接入补丁 2026-09-21 (Bug-1): 批次「输入内容」哈希,用于缓存命中判定。

    排除 batch_id(它是缓存文件名/键本身,不是输入内容),其余字段做确定性序列化。
    这是 ids_hash() 的等价强化版——ids_hash 只认 ids 字段,而各调用方批次结构不一
    (make_batches 用 ids/items、07 归档用 record_ids、07 重命名用 event),
    故改用「整批去 batch_id 后的内容哈希」统一覆盖所有调用方,语义上包含 ids_hash。
    应同步上游。
    """
    payload = {k: v for k, v in batch.items() if k != "batch_id"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def run_batches(batches: list[dict], worker: Callable[[dict], dict],
                raw_dir: Path, concurrency: int = 8,
                label: str = "batch") -> tuple[list[dict], list[dict]]:
    """并发执行，以 raw/<batch_id>.json 存在性做断点续跑。返回 (结果, 失败)。

    Ada接入补丁 2026-09-21 (Bug-1 根因修复): 命中判定原本是「位置型」——只看
    <batch_id>.json 是否存在就复用,不比对输入内容。换了输入(不同 record 集)但
    batch 名相同 / run-dir 复用时,旧缓存会被当命中而复用 → 数据串味。现额外把每批的
    输入内容哈希落到 sidecar(raw_dir/_input_hashes/hashes.json),续跑时比对哈希:
    一致才复用,不一致视为未命中并重跑(覆盖旧缓存与哈希)。sidecar 放子目录,避开
    x1_presplit_blocks 对 raw/block_archive 的非递归 `*.json` glob,且不改
    <batch_id>.json 输出文件结构(向后兼容)。旧缓存无 sidecar 记录时按旧语义信任
    并回填哈希(不强制整轮重跑,避免误伤在途历史 run);新写入一律带哈希。应同步上游。
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    hash_dir = raw_dir / "_input_hashes"
    hash_path = hash_dir / "hashes.json"
    stored_hashes: dict[str, str] = {}
    if hash_path.exists():
        try:
            stored_hashes = json.loads(hash_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stored_hashes = {}
    hash_lock = threading.Lock()

    def _persist_hashes() -> None:
        hash_dir.mkdir(parents=True, exist_ok=True)
        try:
            hash_path.write_text(
                json.dumps(stored_hashes, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    results: list[dict] = []
    failures: list[dict] = []
    todo = []
    backfilled = False
    for batch in batches:
        cached = raw_dir / f"{batch['batch_id']}.json"
        if cached.exists():
            cur_hash = _batch_input_hash(batch)
            prev = stored_hashes.get(batch["batch_id"])
            if prev is None:
                # 补丁前写入的旧缓存: 无哈希记录,按旧语义信任并回填
                stored_hashes[batch["batch_id"]] = cur_hash
                backfilled = True
                results.append(json.loads(cached.read_text(encoding="utf-8")))
            elif prev == cur_hash:
                results.append(json.loads(cached.read_text(encoding="utf-8")))
            else:
                print(f"[{label}] {batch['batch_id']} 输入内容变化"
                      f"（缓存哈希 {prev} → 当前 {cur_hash}），缓存失效,重跑",
                      flush=True)
                todo.append(batch)
        else:
            todo.append(batch)
    if backfilled:
        _persist_hashes()
    done = len(results)
    print(f"[{label}] 共 {len(batches)} 批，已完成 {done}，待跑 {len(todo)}", flush=True)
    if not todo:
        return results, failures

    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(worker, b): b for b in todo}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                payload = fut.result()
            except Exception as exc:  # 单批失败不拖垮整轮，记录后继续
                failures.append({"batch_id": batch["batch_id"], "error": repr(exc)})
                print(f"[{label}] {batch['batch_id']} 失败：{exc!r}", flush=True)
                continue
            (raw_dir / f"{batch['batch_id']}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            # Ada接入补丁 2026-09-21 (Bug-1): 输出落盘的同时记录该批输入哈希,
            # 供后续续跑比对(与逐批写 <batch_id>.json 同频,保证崩溃后进度一致)。
            with hash_lock:
                stored_hashes[batch["batch_id"]] = _batch_input_hash(batch)
                _persist_hashes()
            with lock:
                results.append(payload)
                progress = len(results)
            print(f"[{label}] {batch['batch_id']} 完成 ({progress}/{len(batches)})",
                  flush=True)
    return results, failures


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> int:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig 让 Excel 直接认中文
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    return len(rows)


def ids_hash(ids: Sequence[str]) -> str:
    """工作单元 ID 集合的哈希，不是全量输入文件哈希。
    用文件哈希的话，任何一条无关记录变动都会让整轮无法复用。"""
    return hashlib.sha1("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]


def log_run(manifest_path: Path, entry: dict) -> None:
    """append-only 写成本台账。覆盖写会丢掉返工阶段的真实花费。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"cost_ledger": [], "runs": []}
    manifest.setdefault("cost_ledger", []).append(
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def report(relay: Relay, stage: str, manifest: Path | None = None,
           extra: dict | None = None) -> None:
    usage = relay.usage
    cost = relay.cost()
    print(f"[{stage}] 调用 {relay.calls} 次 | in {usage['input_tokens']} "
          f"out {usage['output_tokens']} | cache_read {usage['cache_read_input_tokens']} "
          f"| 估算 ${cost:.4f}", flush=True)
    if usage["cache_read_input_tokens"] == 0 and relay.calls > 1:
        print(f"[{stage}] 提示缓存未命中，system 块可能未达模型最小 token 门槛", flush=True)
    if manifest:
        log_run(manifest, {"stage": stage, "model": relay.model, "calls": relay.calls,
                           "cost_usd": round(cost, 5), **usage, **(extra or {})})


def require_full_coverage(expected: Sequence[str], got: Sequence[str],
                          stage: str) -> list[str]:
    """返回缺失的 ID。调用方必须处理缺失，不能静默丢数据。"""
    missing = [i for i in expected if i not in set(got)]
    if missing:
        print(f"[{stage}] 模型漏返 {len(missing)} 条，将走本地兜底：{missing[:5]}",
              file=sys.stderr, flush=True)
    return missing
