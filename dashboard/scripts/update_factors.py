#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from typing import Any

import akshare as ak
import pandas as pd
import requests


APP_ROOT = Path(__file__).resolve().parents[1]
ROOT = APP_ROOT.parent
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "factor-history.sqlite3"
OUTPUT_PATH = DATA_DIR / "factor-data.json"
SOURCE = "中证指数有限公司（经 AkShare 获取）"

# These are official price indices. Derived relative-strength series are only
# research comparisons and are never stored or presented as new indices.
SERIES_CATALOG = [
    ("000906", "中证800", "市场基准", "中证800指数，覆盖沪深大中盘股票。"),
    ("000300", "沪深300", "大盘基准", "沪深两市代表性大市值股票。"),
    ("000905", "中证500", "中盘", "沪深300以外的中等市值股票。"),
    ("000852", "中证1000", "小盘", "中证800以外的小市值股票。"),
    ("932000", "中证2000", "微盘", "中证1000以外、更小市值股票。"),
    ("H30355", "中证800成长", "成长", "中证800股票池内的成长风格指数。"),
    ("H30356", "中证800价值", "价值", "中证800股票池内的价值风格指数。"),
    ("932433", "中证800质量", "质量", "中证800股票池内的质量风格指数。"),
    ("930847", "中证800行业中性低波动", "低波动", "中证800股票池内、控制行业暴露的低波动指数。"),
    ("H30260", "沪深300动量", "动量", "沪深300股票池内的动量风格指数。"),
    ("931644", "中证800红利", "红利", "中证800股票池内的红利风格指数。"),
    ("931848", "中证800红利低波动", "红利低波", "中证800股票池内的红利低波动复合风格指数。"),
]

FACTOR_CATALOG = [
    {
        "id": "size",
        "name": "市值结构",
        "category": "市场结构",
        "description": "这不是自行构建的“规模因子”。用四档官方市值指数回答一个更直接的问题：市场近年更偏好大盘、中盘、小盘还是微盘公司。跨层级比较请看相对收益；四条原始指数不在同一天发布。",
        "lines": ["000300", "000905", "000852", "932000"],
        "comparisons": [
            ("000852", "000300", "小盘 / 大盘"),
            ("932000", "000300", "微盘 / 大盘"),
        ],
    },
    {
        "id": "value",
        "name": "价值",
        "category": "核心风格",
        "description": "在同一中证800股票池内比较价值与成长，避免把市值差异混入风格判断。",
        "lines": ["H30356", "H30355"],
        "comparisons": [("H30356", "H30355", "价值 / 成长")],
    },
    {
        "id": "quality",
        "name": "质量",
        "category": "核心风格",
        "description": "中证800质量相对中证800的表现，是盈利质量风格的官方指数代理，不等同于严格的 Fama-French RMW 多空组合。",
        "lines": ["932433", "000906"],
        "comparisons": [("932433", "000906", "质量 / 中证800")],
    },
    {
        "id": "lowvol",
        "name": "低波动",
        "category": "核心风格",
        "description": "中证800行业中性低波动相对中证800，尽量减少行业配置对低波风格判断的干扰。",
        "lines": ["930847", "000906"],
        "comparisons": [("930847", "000906", "低波 / 中证800")],
    },
    {
        "id": "momentum",
        "name": "动量",
        "category": "核心风格",
        "description": "中证目前可用的官方动量代理为沪深300动量；它使用沪深300股票池，因此不与中证800风格作严格横向排名。",
        "lines": ["H30260", "000300"],
        "comparisons": [("H30260", "000300", "动量 / 沪深300")],
    },
    {
        "id": "dividend",
        "name": "红利",
        "category": "组合型风格",
        "description": "红利通常同时带有价值、质量与行业暴露；本页将其作为组合型风格，而非独立经典因子。",
        "lines": ["931644", "000906"],
        "comparisons": [("931644", "000906", "红利 / 中证800")],
    },
    {
        "id": "dividend_lowvol",
        "name": "红利低波",
        "category": "组合型风格",
        "description": "红利低波是红利、稳定性和低波动的复合风格，适合与红利及低波动同时观察。",
        "lines": ["931848", "931644", "930847", "000906"],
        "comparisons": [
            ("931848", "000906", "红利低波 / 中证800"),
            ("931848", "931644", "红利低波 / 红利"),
        ],
    },
]

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS indexes (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    methodology TEXT NOT NULL,
    first_seen_date TEXT,
    last_seen_date TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_metrics (
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    official_name TEXT,
    close REAL,
    change_pct REAL,
    rolling_pe REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, code),
    FOREIGN KEY (code) REFERENCES indexes(code)
);
CREATE INDEX IF NOT EXISTS idx_factor_daily_code_date ON daily_metrics(code, trade_date);
CREATE TABLE IF NOT EXISTS current_components (
    code TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    weight REAL,
    component_date TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, stock_code),
    FOREIGN KEY (code) REFERENCES indexes(code)
);
CREATE INDEX IF NOT EXISTS idx_factor_components_code ON current_components(code);
"""


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    connection.executemany(
        """
        INSERT INTO indexes(code, name, role, methodology, updated_at) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET name=excluded.name, role=excluded.role,
          methodology=excluded.methodology, updated_at=excluded.updated_at
        """,
        [(*item, now) for item in SERIES_CATALOG],
    )
    connection.execute("DELETE FROM daily_metrics WHERE trade_date='1990-01-01'")
    connection.commit()
    return connection


def fetch_history(code: str, start: date, end: date) -> list[dict[str, Any]]:
    frame = ak.stock_zh_index_hist_csindex(
        symbol=code, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
    )
    if frame.empty:
        return []
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    rows = []
    for _, item in frame.dropna(subset=["日期"]).iterrows():
        trade_date = item["日期"]
        if trade_date == date(1990, 1, 1) or not start <= trade_date <= end:
            continue
        rows.append({
            "trade_date": trade_date.isoformat(),
            "code": code,
            "official_name": str(item.get("指数中文全称", "")).strip(),
            "close": number(item.get("收盘")),
            "change_pct": number(item.get("涨跌幅")),
            "rolling_pe": number(item.get("滚动市盈率")),
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return rows


def upsert_history(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO daily_metrics(trade_date, code, official_name, close, change_pct, rolling_pe, fetched_at)
        VALUES(:trade_date, :code, :official_name, :close, :change_pct, :rolling_pe, :fetched_at)
        ON CONFLICT(trade_date, code) DO UPDATE SET official_name=excluded.official_name,
          close=excluded.close, change_pct=excluded.change_pct, rolling_pe=excluded.rolling_pe,
          fetched_at=excluded.fetched_at
        """,
        rows,
    )
    for code, _, _, _ in SERIES_CATALOG:
        bounds = connection.execute(
            "SELECT min(trade_date), max(trade_date) FROM daily_metrics WHERE code=?", (code,)
        ).fetchone()
        if bounds[0]:
            connection.execute(
                "UPDATE indexes SET first_seen_date=?, last_seen_date=? WHERE code=?",
                (bounds[0], bounds[1], code),
            )


def fetch_components(code: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Fetch the current official constituent weights for one CSI index.

    Bypasses akshare's index_stock_cons_weight_csindex, which issues its
    request with no timeout: a stalled response there blocks for minutes
    until the peer resets the connection, which can burn the whole CI job
    budget on a single slow index.
    """
    url = (
        "https://oss-ch.csindex.com.cn/static/html/csindex/"
        f"public/uploads/file/autofile/closeweight/{code}closeweight.xls"
    )
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    frame = pd.read_excel(BytesIO(response.content))
    frame.columns = [
        "日期", "指数代码", "指数名称", "指数英文名称",
        "成分券代码", "成分券名称", "成分券英文名称",
        "交易所", "交易所英文名称", "权重",
    ]
    if frame.empty:
        return []
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []
    for _, item in frame.iterrows():
        stock_code = str(item.get("成分券代码", "")).strip()
        stock_name = str(item.get("成分券名称", "")).strip()
        if not stock_code or not stock_name:
            continue
        component_date = item.get("日期")
        if hasattr(component_date, "isoformat"):
            component_date = component_date.isoformat()
        rows.append({
            "code": code,
            "stock_code": stock_code.zfill(6),
            "stock_name": stock_name,
            "weight": number(item.get("权重")),
            "component_date": str(component_date) if component_date else None,
            "fetched_at": fetched_at,
        })
    return rows


def replace_components(connection: sqlite3.Connection, code: str, rows: list[dict[str, Any]]) -> None:
    """Replace a code only after its complete official component file was fetched."""
    if not rows:
        return
    connection.execute("DELETE FROM current_components WHERE code=?", (code,))
    connection.executemany(
        """
        INSERT INTO current_components(code, stock_code, stock_name, weight, component_date, fetched_at)
        VALUES(:code, :stock_code, :stock_name, :weight, :component_date, :fetched_at)
        """,
        rows,
    )


def shift_year(value: date, years: int) -> date:
    return date(value.year + years, value.month, min(value.day, calendar.monthrange(value.year + years, value.month)[1]))


def value_on_or_before(points: list[dict[str, Any]], target: date, key: str = "close") -> float | None:
    eligible = [point[key] for point in points if date.fromisoformat(point["date"]) <= target and point.get(key) is not None]
    return eligible[-1] if eligible else None


def period_return(points: list[dict[str, Any]], current: float | None, target: date, key: str = "close") -> float | None:
    start = value_on_or_before(points, target, key)
    return current / start - 1 if current is not None and start else None


def percentile(values: list[float], current: float | None) -> float | None:
    return sum(value <= current for value in values) / len(values) if current is not None and values else None


def monthly_points(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    month_end: dict[str, sqlite3.Row] = {}
    for row in rows:
        month_end[row["trade_date"][:7]] = row
    return [
        {"date": row["trade_date"], "close": row["close"], "pe": row["rolling_pe"]}
        for row in month_end.values() if row["close"] is not None
    ]


def make_relative(primary: dict[str, Any], comparator: dict[str, Any], label: str) -> dict[str, Any]:
    left = {point["date"]: point["close"] for point in primary["history"] if point["close"] is not None}
    right = {point["date"]: point["close"] for point in comparator["history"] if point["close"] is not None}
    dates = sorted(set(left) & set(right))
    if not dates:
        return {"label": label, "primaryCode": primary["code"], "comparatorCode": comparator["code"], "history": [], "metrics": {}}
    first_ratio = left[dates[0]] / right[dates[0]]
    history = [{"date": trade_date, "level": left[trade_date] / right[trade_date] / first_ratio * 100} for trade_date in dates]
    as_of = date.fromisoformat(history[-1]["date"])
    levels = [point["level"] for point in history]
    peak = levels[0]
    drawdowns = []
    for level in levels:
        peak = max(peak, level)
        drawdowns.append(level / peak - 1)
    current = levels[-1]
    metrics = {
        "return1y": period_return(history, current, shift_year(as_of, -1), "level"),
        "return3y": period_return(history, current, shift_year(as_of, -3), "level"),
        "return5y": period_return(history, current, shift_year(as_of, -5), "level"),
        "percentile": percentile(levels, current),
        "maxDrawdown": min(drawdowns) if drawdowns else None,
        "currentLevel": current,
    }
    return {
        "label": label,
        "primaryCode": primary["code"],
        "comparatorCode": comparator["code"],
        "history": history,
        "metrics": metrics,
    }


def load_sector_mapping() -> dict[str, str]:
    """Use the same current Shenwan primary-industry mapping as the market page."""
    database = ROOT / "industry" / "data" / "industry-history.sqlite3"
    if not database.exists():
        return {}
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT c.stock_code, s.current_name
            FROM current_index_components AS c
            JOIN index_series AS s ON s.code = c.code
            """
        ).fetchall()
    finally:
        connection.close()
    return {str(stock_code).zfill(6): name for stock_code, name in rows if name}


def load_component_details(connection: sqlite3.Connection, mapping: dict[str, str]) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT code, stock_code, stock_name, weight, component_date
        FROM current_components
        ORDER BY code, weight DESC, stock_code
        """
    ).fetchall()
    details: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = details.setdefault(row["code"], {"components": [], "sectorWeights": {}, "componentDate": row["component_date"]})
        component = {
            "code": row["stock_code"],
            "name": row["stock_name"],
            "weight": row["weight"],
            "date": row["component_date"],
        }
        entry["components"].append(component)
        if row["weight"] is not None:
            sector = mapping.get(str(row["stock_code"]).zfill(6), "未分类")
            entry["sectorWeights"][sector] = entry["sectorWeights"].get(sector, 0) + float(row["weight"])
    for entry in details.values():
        entry["components"] = entry["components"][:10]
        entry["sectorAllocation"] = [
            {"name": name, "weight": round(weight, 4)}
            for name, weight in sorted(entry.pop("sectorWeights").items(), key=lambda pair: pair[1], reverse=True)
        ]
    return details


def build_factor_data(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    latest_dates = [row[0] for row in connection.execute("SELECT max(trade_date) FROM daily_metrics GROUP BY code")]
    if not latest_dates:
        raise RuntimeError("no factor index data available")
    as_of = min(latest_dates)
    catalog = {code: (name, role, methodology) for code, name, role, methodology in SERIES_CATALOG}
    component_details = load_component_details(connection, load_sector_mapping())
    series: dict[str, dict[str, Any]] = {}
    for code, (name, role, methodology) in catalog.items():
        rows = connection.execute(
            "SELECT * FROM daily_metrics WHERE code=? AND trade_date<=? ORDER BY trade_date", (code, as_of)
        ).fetchall()
        if not rows:
            continue
        history = monthly_points(rows)
        current = history[-1]
        as_of_date = date.fromisoformat(as_of)
        pe_values = [point["pe"] for point in history if point["pe"] is not None and point["pe"] > 0]
        series[code] = {
            "code": code,
            # Upstream historical names such as "中证小盘500指数" are aliases.
            # Keep one stable, familiar display name while retaining the official code.
            "name": name,
            "role": role,
            "methodology": methodology,
            "coverageStart": history[0]["date"],
            "close": current["close"],
            "pe": current["pe"],
            "pePercentile": percentile(pe_values, current["pe"]),
            "returnYtd": period_return(history, current["close"], date(as_of_date.year - 1, 12, 31)),
            "return1y": period_return(history, current["close"], shift_year(as_of_date, -1)),
            "return3y": period_return(history, current["close"], shift_year(as_of_date, -3)),
            "return5y": period_return(history, current["close"], shift_year(as_of_date, -5)),
            "history": history,
            "components": component_details.get(code, {}).get("components", []),
            "componentDate": component_details.get(code, {}).get("componentDate"),
            "sectorAllocation": component_details.get(code, {}).get("sectorAllocation", []),
        }

    factors = []
    for definition in FACTOR_CATALOG:
        lines = [series[code] for code in definition["lines"] if code in series]
        comparisons = [
            make_relative(series[primary], series[comparator], label)
            for primary, comparator, label in definition["comparisons"]
            if primary in series and comparator in series
        ]
        if lines and comparisons:
            factors.append({
                "id": definition["id"],
                "name": definition["name"],
                "category": definition["category"],
                "description": definition["description"],
                "lines": lines,
                "comparisons": comparisons,
            })
    return {"asOf": as_of, "source": SOURCE, "factors": factors}


def write_dashboard(connection: sqlite3.Connection) -> None:
    data = build_factor_data(connection)
    OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}: {len(data['factors'])} factor groups as of {data['asOf']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="更新中证官方因子指数数据")
    parser.add_argument("--full", action="store_true", help="完整回填全部可用历史")
    parser.add_argument("--start-date", help="覆盖自动起点，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--dashboard-only", action="store_true", help="只从数据库生成前端数据")
    args = parser.parse_args()
    connection = connect()
    if args.dashboard_only:
        write_dashboard(connection)
        connection.close()
        return
    latest = connection.execute("SELECT max(trade_date) FROM daily_metrics").fetchone()[0]
    start = date.fromisoformat(args.start_date) if args.start_date else (date(1990, 1, 1) if args.full or not latest else date.fromisoformat(latest) - timedelta(days=14))
    end = date.fromisoformat(args.end_date)
    if start > end:
        raise SystemExit("start date is after end date")
    try:
        for code, name, _, _ in SERIES_CATALOG:
            rows = fetch_history(code, start, end)
            with connection:
                upsert_history(connection, rows)
            print(f"fetched {code} {name}: {len(rows)} rows", flush=True)
        for code, name, _, _ in SERIES_CATALOG:
            try:
                components = fetch_components(code)
                with connection:
                    replace_components(connection, code, components)
                print(f"fetched {code} {name} components: {len(components)} rows", flush=True)
            except Exception as error:
                # Preserve the last complete constituent file if a transient request fails.
                print(f"warning {code} {name} components: {error}", flush=True)
        write_dashboard(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
