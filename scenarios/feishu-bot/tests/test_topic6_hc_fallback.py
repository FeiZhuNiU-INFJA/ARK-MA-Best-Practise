"""topic6 HC 兜底文本意图检测:Agent 违约时靠正文关键词把 HC 卡片救回来。

- detect_hc_intent:纯函数,匹配文本 → 返回 HC1/HC2/HC3 或 None。
- extract_hc_payload 走的严格 JSON 路径不受影响(留在 test_topic6_new_command / 现有测试)。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(name: str, file: str):
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CASE_DIR / file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner_mod = _load("topic6_runner", "topic6_runner.py")
detect_hc_intent = runner_mod.detect_hc_intent
extract_hc_payload = runner_mod.extract_hc_payload


def test_valid_json_takes_priority_over_fallback():
    # 严格 JSON 存在时,现有 extract_hc_payload 已经吃掉;兜底不会覆盖。
    text = '{"hc": "HC1", "mode": "test"}'
    assert extract_hc_payload(text) == {"hc": "HC1", "mode": "test"}


def test_intent_detects_bad_meta_description():
    # 复现截图1里 Agent 说过的原话——违约但意图明确。
    text = "宽表已就绪,累计 tokens 1126万、成本 ¥1.24。现在输出 HC1 结构化 JSON(等用户点击卡片确认):"
    assert detect_hc_intent(text) == "HC1"
    # 严格 JSON 路径拿不到,是兜底该救的场景。
    assert extract_hc_payload(text) is None


def test_intent_detects_hc2_and_hc3_wording_variants():
    assert detect_hc_intent("HC2 已就绪,请审核后点击卡片") == "HC2"
    assert detect_hc_intent("接下来输出 HC3,请点击卡片确认") == "HC3"


def test_intent_ignores_hc_mention_without_intent_keyword():
    # 只在流水线介绍里提了一嘴 HC,没有"等用户/请审核"这类意图关键词 → 不误触发。
    assert detect_hc_intent("流程包含 HC1 / HC2 / HC3 三个人工卡点,当前还在跑数。") is None


def test_intent_ignores_intent_words_without_hc_marker():
    # 有"等用户点击卡片确认"但没 HC 类型 → 不触发。
    assert detect_hc_intent("现在等用户点击卡片确认导入下一阶段。") is None


def test_intent_returns_none_on_empty():
    assert detect_hc_intent("") is None
    assert detect_hc_intent(None) is None  # type: ignore[arg-type]
