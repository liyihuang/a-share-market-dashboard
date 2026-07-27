#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

import akshare as ak
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "csi-market-history.sqlite3"
OUTPUT_PATH = DATA_DIR / "dashboard-data.js"
SOURCE = "中证指数有限公司；创业板指为深交所指数（均经 AkShare 获取）"

INDEX_CATALOG = [
    ("000985", "中证全指", "全市场基准", "覆盖符合条件的沪深 A 股，反映市值加权的整体市场表现。"),
    ("000510", "中证A500", "均衡核心宽基", "行业覆盖相对均衡的 500 只代表性 A 股，不属于市值阶梯。"),
    ("000300", "沪深300", "大盘", "代表性大市值 A 股。"),
    ("000905", "中证500", "中盘", "沪深300之外的中等市值 A 股。"),
    ("000852", "中证1000", "小盘", "中证800之外的小市值 A 股。"),
    ("932000", "中证2000", "微盘", "中证1000之外、更小市值的 A 股。"),
    ("399006", "创业板指", "成长板块补充", "深交所创业板代表性股票组成的价格指数；不属于中证市值阶梯。"),
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
    volume REAL,
    amount REAL,
    sample_count REAL,
    rolling_pe REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, code),
    FOREIGN KEY (code) REFERENCES indexes(code)
);
CREATE INDEX IF NOT EXISTS idx_daily_metrics_code_date ON daily_metrics(code, trade_date);
CREATE TABLE IF NOT EXISTS current_components (
    code TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    weight REAL,
    weight_date TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, stock_code),
    FOREIGN KEY (code) REFERENCES indexes(code)
);
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
    # CSI's public history response prefixes each series with a synthetic
    # 1990-01-01 row. Keep it out even when only rebuilding dashboard output.
    connection.execute("DELETE FROM daily_metrics WHERE trade_date='1990-01-01'")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    connection.executemany(
        """
        INSERT INTO indexes(code, name, role, methodology, updated_at) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET name=excluded.name, role=excluded.role,
          methodology=excluded.methodology, updated_at=excluded.updated_at
        """,
        [(*item, now) for item in INDEX_CATALOG],
    )
    connection.commit()
    return connection


def fetch_history(code: str, start: date, end: date) -> list[dict[str, Any]]:
    if code == "399006":
        frame = ak.stock_zh_index_daily(symbol="sz399006")
        if frame.empty:
            return []
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.dropna(subset=["date"]).sort_values("date")
        rows = []
        previous_close = None
        for _, item in frame.iterrows():
            trade_date = item["date"]
            close = number(item.get("close"))
            change_pct = (close / previous_close - 1) * 100 if close is not None and previous_close else None
            if start <= trade_date <= end:
                rows.append({
                    "trade_date": trade_date.isoformat(),
                    "code": code,
                    "official_name": "创业板指",
                    "close": close,
                    "change_pct": change_pct,
                    "volume": number(item.get("volume")),
                    "amount": None,
                    "sample_count": None,
                    "rolling_pe": None,
                    "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                })
            if close is not None:
                previous_close = close
        return rows
    frame = ak.stock_zh_index_hist_csindex(
        symbol=code, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
    )
    if frame.empty:
        return []
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    rows = []
    for _, item in frame.dropna(subset=["日期"]).iterrows():
        trade_date = item["日期"]
        # The public endpoint prefixes every series with a 1990-01-01
        # placeholder. It is not an observed or back-calculated index date.
        if trade_date == date(1990, 1, 1) or not start <= trade_date <= end:
            continue
        rows.append({
            "trade_date": trade_date.isoformat(),
            "code": code,
            "official_name": str(item.get("指数中文全称", "")).strip(),
            "close": number(item.get("收盘")),
            "change_pct": number(item.get("涨跌幅")),
            "volume": number(item.get("成交量")),
            "amount": number(item.get("成交金额")),
            "sample_count": number(item.get("样本数量")),
            "rolling_pe": number(item.get("滚动市盈率")),
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return rows


def upsert_history(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO daily_metrics(trade_date, code, official_name, close, change_pct, volume, amount, sample_count, rolling_pe, fetched_at)
        VALUES(:trade_date, :code, :official_name, :close, :change_pct, :volume, :amount, :sample_count, :rolling_pe, :fetched_at)
        ON CONFLICT(trade_date, code) DO UPDATE SET official_name=excluded.official_name, close=excluded.close,
          change_pct=excluded.change_pct, volume=excluded.volume, amount=excluded.amount,
          sample_count=excluded.sample_count, rolling_pe=excluded.rolling_pe, fetched_at=excluded.fetched_at
        """, rows,
    )
    for code, _, _, _ in INDEX_CATALOG:
        connection.execute("DELETE FROM daily_metrics WHERE code=? AND trade_date='1990-01-01'", (code,))
        bounds = connection.execute("SELECT min(trade_date), max(trade_date) FROM daily_metrics WHERE code=?", (code,)).fetchone()
        if bounds[0]:
            connection.execute("UPDATE indexes SET first_seen_date=?, last_seen_date=? WHERE code=?", (bounds[0], bounds[1], code))


def refresh_components(connection: sqlite3.Connection) -> None:
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for code, _, _, _ in INDEX_CATALOG:
        try:
            if code == "399006":
                # AkShare exposes the constituent list for the SZSE index,
                # but not a verified public weight file.
                frame = ak.index_stock_cons(symbol=code)
            else:
                frame = ak.index_stock_cons_weight_csindex(symbol=code)
        except Exception as error:
            print(f"warning: components {code}: {error}", flush=True)
            continue
        if frame.empty:
            continue
        rows = []
        for _, item in frame.iterrows():
            stock_code = str(item.get("成分券代码", item.get("品种代码", ""))).zfill(6)
            stock_name = str(item.get("成分券名称", item.get("品种名称", ""))).strip()
            if not stock_name:
                continue
            weight_date = item.get("日期", item.get("纳入日期"))
            weight = number(item.get("权重")) if code != "399006" else None
            rows.append((code, stock_code, stock_name, weight, str(weight_date)[:10], fetched_at))
        if not rows:
            continue
        connection.execute("DELETE FROM current_components WHERE code=?", (code,))
        connection.executemany("INSERT INTO current_components(code, stock_code, stock_name, weight, weight_date, fetched_at) VALUES (?, ?, ?, ?, ?, ?)", rows)
        print(f"fetched components {code}: {len(rows)} stocks", flush=True)


def shift_year(value: date, years: int) -> date:
    return date(value.year + years, value.month, min(value.day, calendar.monthrange(value.year + years, value.month)[1]))


def price_on_or_before(rows: list[sqlite3.Row], target: date) -> float | None:
    eligible = [row["close"] for row in rows if date.fromisoformat(row["trade_date"]) <= target and row["close"] is not None]
    return eligible[-1] if eligible else None


def period_return(rows: list[sqlite3.Row], current: float | None, target: date) -> float | None:
    start = price_on_or_before(rows, target)
    return current / start - 1 if current is not None and start else None


def percentile(values: list[float], current: float | None) -> float | None:
    return sum(value <= current for value in values) / len(values) if current is not None and values else None


def build_dashboard(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    latest_by_code = [row[0] for row in connection.execute("SELECT max(trade_date) FROM daily_metrics GROUP BY code")]
    if not latest_by_code:
        raise RuntimeError("no index data available")
    as_of = min(latest_by_code)
    indexes = []
    for code, fallback_name, role, methodology in INDEX_CATALOG:
        rows = connection.execute("SELECT * FROM daily_metrics WHERE code=? AND trade_date<=? ORDER BY trade_date", (code, as_of)).fetchall()
        if not rows:
            continue
        current = rows[-1]
        month_end: dict[str, sqlite3.Row] = {}
        for row in rows:
            month_end[row["trade_date"][:7]] = row
        pe_values = [row["rolling_pe"] for row in rows if row["rolling_pe"] is not None and row["rolling_pe"] > 0]
        components = connection.execute("SELECT stock_code, stock_name, weight, weight_date FROM current_components WHERE code=? ORDER BY weight DESC, stock_code LIMIT 10", (code,)).fetchall()
        indexes.append({
            "code": code,
            "name": current["official_name"] or fallback_name,
            "role": role,
            "methodology": (
                f"{methodology} 本页使用深交所公开价格行情，不含分红；"
                "历史起点为公开历史数据起点。"
                if code == "399006" else
                f"{methodology} 本页使用中证官方价格指数，不含分红；样本会按官方规则定期调整。历史起点为中证公开的历史计算起点，不等同于产品发布日。"
            ),
            "coverageStart": rows[0]["trade_date"],
            "close": current["close"],
            "changePct": current["change_pct"],
            "pe": current["rolling_pe"],
            "pePercentile": percentile(pe_values, current["rolling_pe"]),
            "returnYtd": period_return(rows, current["close"], date(date.fromisoformat(as_of).year - 1, 12, 31)),
            "return1y": period_return(rows, current["close"], shift_year(date.fromisoformat(as_of), -1)),
            "return3y": period_return(rows, current["close"], shift_year(date.fromisoformat(as_of), -3)),
            "return5y": period_return(rows, current["close"], shift_year(date.fromisoformat(as_of), -5)),
            "history": [{"date": row["trade_date"], "close": row["close"], "pe": row["rolling_pe"]} for row in month_end.values()],
            "components": [{"code": row["stock_code"], "name": row["stock_name"], "weight": row["weight"], "date": row["weight_date"]} for row in components],
            "componentDate": components[0]["weight_date"] if components else None,
        })
    return {"asOf": as_of, "source": SOURCE, "indexes": indexes}


def write_dashboard(connection: sqlite3.Connection) -> None:
    dashboard = build_dashboard(connection)
    OUTPUT_PATH.write_text("window.CSI_MARKET_DATA = " + json.dumps(dashboard, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}: {len(dashboard['indexes'])} indexes as of {dashboard['asOf']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="更新中证市场价格指数看板")
    parser.add_argument("--full", action="store_true", help="从 1990-01-01 回填全部可用历史")
    parser.add_argument("--start-date", help="覆盖自动起点，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--skip-components", action="store_true")
    parser.add_argument("--dashboard-only", action="store_true")
    args = parser.parse_args()
    connection = connect()
    if args.dashboard_only:
        write_dashboard(connection); connection.close(); return
    latest = connection.execute("SELECT max(trade_date) FROM daily_metrics").fetchone()[0]
    start = date.fromisoformat(args.start_date) if args.start_date else (date(1990, 1, 1) if args.full or not latest else date.fromisoformat(latest) - timedelta(days=14))
    end = date.fromisoformat(args.end_date)
    if start > end:
        raise SystemExit("start date is after end date")
    try:
        for code, name, _, _ in INDEX_CATALOG:
            rows = fetch_history(code, start, end)
            with connection:
                upsert_history(connection, rows)
            print(f"fetched {code} {name}: {len(rows)} rows", flush=True)
        if not args.skip_components:
            with connection:
                refresh_components(connection)
        write_dashboard(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
