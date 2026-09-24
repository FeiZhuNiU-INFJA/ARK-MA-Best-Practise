#!/usr/bin/env python3
"""
营销节点日历生成器
用途：输入年份，自动生成该年的营销节点 By-Day Markdown 表格
支持：2025–2035（节气内置查找表）；其他年份需安装 ephem 库

Usage:
    python generate_marketing_calendar.py 2027
    python generate_marketing_calendar.py 2030 --output /path/to/output.md

依赖：
    pip install lunardate          # 农历节日（必须）
    pip install ephem              # 2025-2035 以外年份的节气计算（可选）
"""

from __future__ import annotations

import sys
import io
import csv
import math
import argparse
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

SCRIPT_DIR = Path(__file__).parent

# ──────────────────────────────────────────────
# 1. 节气预计算表（UTC+8，覆盖 2025-2035）
# ──────────────────────────────────────────────
SOLAR_TERM_TABLE = {
    2025: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-20","立夏":"05-05","小满":"05-21","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-22","立秋":"08-07","处暑":"08-22","白露":"09-07","秋分":"09-22","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-21"},
    2026: {"小寒":"01-05","大寒":"01-20","立春":"02-04","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-20","立夏":"05-05","小满":"05-21","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-23","立秋":"08-07","处暑":"08-23","白露":"09-07","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-22"},
    2027: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-05","谷雨":"04-20","立夏":"05-05","小满":"05-21","芒种":"06-06","夏至":"06-21","小暑":"07-07","大暑":"07-23","立秋":"08-07","处暑":"08-23","白露":"09-08","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-22"},
    2028: {"小寒":"01-06","大寒":"01-20","立春":"02-04","雨水":"02-19","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-19","立夏":"05-05","小满":"05-20","芒种":"06-05","夏至":"06-21","小暑":"07-06","大暑":"07-22","立秋":"08-07","处暑":"08-22","白露":"09-07","秋分":"09-22","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-06","冬至":"12-21"},
    2029: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-19","立夏":"05-05","小满":"05-21","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-22","立秋":"08-07","处暑":"08-23","白露":"09-07","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-21"},
    2030: {"小寒":"01-05","大寒":"01-20","立春":"02-04","雨水":"02-18","惊蛰":"03-06","春分":"03-20","清明":"04-05","谷雨":"04-20","立夏":"05-06","小满":"05-21","芒种":"06-06","夏至":"06-21","小暑":"07-07","大暑":"07-23","立秋":"08-07","处暑":"08-23","白露":"09-08","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-22"},
    2031: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-05","谷雨":"04-20","立夏":"05-05","小满":"05-21","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-23","立秋":"08-07","处暑":"08-23","白露":"09-08","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-22"},
    2032: {"小寒":"01-06","大寒":"01-21","立春":"02-04","雨水":"02-19","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-19","立夏":"05-05","小满":"05-20","芒种":"06-05","夏至":"06-21","小暑":"07-06","大暑":"07-22","立秋":"08-07","处暑":"08-22","白露":"09-07","秋分":"09-22","寒露":"10-07","霜降":"10-22","立冬":"11-06","小雪":"11-21","大雪":"12-06","冬至":"12-21"},
    2033: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-04","谷雨":"04-19","立夏":"05-05","小满":"05-20","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-22","立秋":"08-07","处暑":"08-23","白露":"09-07","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-21"},
    2034: {"小寒":"01-05","大寒":"01-20","立春":"02-04","雨水":"02-18","惊蛰":"03-06","春分":"03-20","清明":"04-05","谷雨":"04-20","立夏":"05-05","小满":"05-21","芒种":"06-06","夏至":"06-21","小暑":"07-07","大暑":"07-23","立秋":"08-07","处暑":"08-23","白露":"09-08","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-22"},
    2035: {"小寒":"01-05","大寒":"01-20","立春":"02-03","雨水":"02-18","惊蛰":"03-05","春分":"03-20","清明":"04-05","谷雨":"04-20","立夏":"05-05","小满":"05-20","芒种":"06-05","夏至":"06-21","小暑":"07-07","大暑":"07-22","立秋":"08-07","处暑":"08-23","白露":"09-07","秋分":"09-23","寒露":"10-08","霜降":"10-23","立冬":"11-07","小雪":"11-22","大雪":"12-07","冬至":"12-21"},
}

TERM_LONGITUDE = {
    "小寒":285,"大寒":300,"立春":315,"雨水":330,"惊蛰":345,"春分":0,
    "清明":15,"谷雨":30,"立夏":45,"小满":60,"芒种":75,"夏至":90,
    "小暑":105,"大暑":120,"立秋":135,"处暑":150,"白露":165,"秋分":180,
    "寒露":195,"霜降":210,"立冬":225,"小雪":240,"大雪":255,"冬至":270,
}

# ──────────────────────────────────────────────
# 2. 辅助函数
# ──────────────────────────────────────────────

def get_solar_term(year: int, term_name: str) -> date | None:
    """获取节气日期，优先查表，其次用 ephem 计算。"""
    if year in SOLAR_TERM_TABLE:
        md = SOLAR_TERM_TABLE[year].get(term_name)
        if md:
            m, d = map(int, md.split("-"))
            return date(year, m, d)
    try:
        return _ephem_solar_term(year, term_name)
    except Exception as e:
        print(f"  ⚠ 节气 {term_name} {year}年 计算失败: {e}", file=sys.stderr)
        return None

def _ephem_solar_term(year: int, term_name: str) -> date:
    import ephem, datetime as dt
    lon_target = TERM_LONGITUDE[term_name]
    # 估算起始日期（小寒 ≈ 1月5日为基准，每15°约15天）
    doy = int(((lon_target - 285) % 360) / 360 * 365.25) + 5
    start = date(year, 1, 1) + timedelta(days=doy - 15)
    d = ephem.Date(start.strftime("%Y/%m/%d"))
    sun = ephem.Sun()
    for _ in range(50):
        sun.compute(d, epoch="2000")
        lon_now = math.degrees(float(sun.hlong)) % 360
        diff = (lon_target - lon_now + 540) % 360 - 180
        if abs(diff) < 0.01:
            break
        d += diff / 360 * ephem.year
    utc_dt = ephem.Date(d).datetime()
    local_dt = utc_dt + dt.timedelta(hours=8)
    return local_dt.date()

def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """第 n 个 weekday（0=Mon…6=Sun）。"""
    d = date(year, month, 1)
    diff = (weekday - d.weekday()) % 7
    first = d + timedelta(days=diff)
    return first + timedelta(weeks=n - 1)

def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """当月最后一个 weekday。"""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    diff = (last.weekday() - weekday) % 7
    return last - timedelta(days=diff)

def lunar_to_solar(year: int, lmonth: int, lday: int) -> date | None:
    try:
        from lunardate import LunarDate
        return LunarDate(year, lmonth, lday).toSolarDate()
    except ImportError:
        print("  ⚠ lunardate 未安装，跳过农历节日。安装：pip install lunardate", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ⚠ 农历 {year}/{lmonth}/{lday} 转换失败: {e}", file=sys.stderr)
        return None

# ──────────────────────────────────────────────
# 3. 核心：计算单个节点的日期
# ──────────────────────────────────────────────

def resolve_date(year: int, row: dict, cached: dict) -> date | None:
    calc = row["calc_type"]
    params = row["params"]

    # 固定公历
    if calc == "fixed_solar":
        m, d = map(int, params.split("-"))
        return date(year, m, d)

    # 浮动公历：MONTH;N;WEEKDAY（N 可以是数字或 "last"）
    # float_solar_last 格式为 MONTH;WEEKDAY（2段），float_solar 为 MONTH;N;WEEKDAY（3段）
    if calc in ("float_solar", "float_solar_last"):
        parts = params.split(";")
        if calc == "float_solar_last" and len(parts) == 2:
            month, wd = int(parts[0]), int(parts[1])
            return last_weekday_of_month(year, month, wd)
        month, n_str, wd = int(parts[0]), parts[1], int(parts[2])
        if n_str == "last":
            return last_weekday_of_month(year, month, wd)
        return nth_weekday(year, month, wd, int(n_str))

    # 浮动公历派生（感恩节+1 = 黑色星期五）
    if calc == "float_solar_derived":
        base_name = params.replace("+1", "").replace("-1", "")
        offset = 1 if "+1" in params else -1
        if base_name in cached:
            return cached[base_name] + timedelta(days=offset)
        return None

    # 节气
    if calc == "solar_term":
        return get_solar_term(year, params)

    # 节气派生（秋分当日 = 中国农民丰收节）
    if calc == "solar_term_derived":
        base = get_solar_term(year, params)
        return base  # 目前均为当日，后续可加 offset

    # 农历
    if calc == "lunar":
        lmonth, lday = map(int, params.split(";"))
        return lunar_to_solar(year, lmonth, lday)

    # 农历派生（春节-1 = 除夕；春节+8 ≈ 返工季）
    if calc == "lunar_derived":
        if "春节" in params:
            base = cached.get("春节") or lunar_to_solar(year, 1, 1)
            if base is None:
                return None
            if "+8" in params:
                # 返工季：春节后第一个工作日（正月初八附近，跳过周末）
                d = base + timedelta(days=7)
                while d.weekday() >= 5:
                    d += timedelta(days=1)
                return d
            offset = int(params.replace("春节", "").replace("+", "") or "0")
            return base + timedelta(days=offset)
        return None

    print(f"  ⚠ 未知 calc_type: {calc}（{row['name']}）", file=sys.stderr)
    return None

# ──────────────────────────────────────────────
# 4. 主流程
# ──────────────────────────────────────────────

def generate(year: int, output_path: Path):
    csv_path = SCRIPT_DIR / "nodes_definition.csv"
    if not csv_path.exists():
        print(f"错误：找不到节点定义文件 {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # 第一遍：计算所有节点（缓存供派生节点使用）
    cached: dict[str, date] = {}
    results: list[tuple[date, str, str, str]] = []  # (date, name, type, notes)

    for row in rows:
        name = row["name"].strip()
        node_type = row["type"].strip()
        notes = row.get("notes", "").strip()
        d = resolve_date(year, row, cached)
        if d is None:
            continue
        if d.year != year:  # 过滤跨年结果
            continue
        cached[name] = d
        results.append((d, name, node_type, notes))

    # 去重（同名节点只保留第一个）
    seen = set()
    deduped = []
    for r in results:
        if r[1] not in seen:
            seen.add(r[1])
            deduped.append(r)

    deduped.sort(key=lambda x: x[0])

    # 写 Markdown
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {year}年营销节点列表（By Day）\n",
        f"> 自动生成，来源：nodes_definition.csv\n",
        f"> 图例：大众节日 / 小众节点 / 二十四节气\n",
        "",
        "| 日期 | 节点名称 | 类型 | 备注 |",
        "|:----:|:--------:|:----:|:----:|",
    ]
    for d, name, ntype, notes in deduped:
        lines.append(f"| {d.strftime('%Y-%m-%d')} | {name} | {ntype} | {notes} |")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 已生成 {len(deduped)} 个节点 → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="营销节点日历生成器")
    parser.add_argument("year", type=int, help="目标年份，如 2027")
    parser.add_argument(
        "--output", type=str,
        help="输出文件路径（默认：{skill_dir}/calendars/YYYY年营销节点_byday.md）"
    )
    args = parser.parse_args()

    if args.output:
        out = Path(args.output)
    else:
        out = SCRIPT_DIR / "calendars" / f"{args.year}年营销节点_byday.md"

    generate(args.year, out)


if __name__ == "__main__":
    main()
