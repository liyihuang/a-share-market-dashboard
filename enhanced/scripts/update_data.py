#!/usr/bin/env python3
"""更新指数增强基金评价数据。

数据链：
1. 中证指数官网 queryByIndexCode
2. 晨星 cn-api performance 接口

采用定制化收益优先评分系统进行综合评分，对风格偏离进行预警。
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import ssl
import time
from typing import Any
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "enhanced-funds.sqlite3"
OUTPUT_PATH = DATA_DIR / "dashboard-data.js"
SOURCE = "中证指数有限公司（跟踪产品清单）；晨星 Morningstar（星级评级与风险指标，全收益基准口径）"

TARGET_INDEXES = [
    ("000985", "中证全指"),
    ("000510", "中证A500"),
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("932000", "中证2000"),
]

CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/index-list/queryByIndexCode/{code}?indexCode={code}"
MORNINGSTAR_URL = "https://www.morningstar.cn/cn-api/v2/funds/{code}/performance"

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS enhanced_funds (
    index_code TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    fund_manager TEXT,
    aum REAL,
    inception_date TEXT,
    risk_date TEXT,
    benchmark TEXT,
    category TEXT,
    
    return5 REAL, excess5 REAL, ir5 REAL, sharpe5 REAL, calmar5 REAL, drawdown5 REAL, stddev5 REAL, batting5 REAL,
    return3 REAL, excess3 REAL, ir3 REAL, sharpe3 REAL,
    excess1 REAL,
    beta5 REAL, r_squared5 REAL, track5 REAL, upside_capture5 REAL, downside_capture5 REAL,
    
    rating_y3 REAL, rating_y5 REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (index_code, fund_code)
);
"""

def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None

def http_json(url: str, attempts: int = 4) -> dict[str, Any]:
    context = ssl._create_unverified_context()
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.morningstar.cn/",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
            )
            with urlopen(request, timeout=60, context=context) as response:
                return json.load(response)
        except Exception as error:
            last_error = error
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed after {attempts} attempts: {url}: {last_error}")

def fetch_enhanced_list(index_code: str) -> list[dict[str, Any]]:
    payload = http_json(CSINDEX_URL.format(code=index_code))
    products = payload.get("data") or []
    funds = []
    for item in products:
        if item.get("fundType") != "指数增强":
            continue
        funds.append({
            "fund_code": str(item.get("productCode", "")).strip(),
            "fund_name": str(item.get("fundName", "")).strip(),
            "fund_manager": str(item.get("fundManager", "")).strip() or None,
            "aum": number(item.get("aum")),
            "inception_date": (str(item.get("inceptionDate", "")).strip() or None),
        })
    return [fund for fund in funds if fund["fund_code"] and fund["fund_name"]]

def extract_risk_metrics(risk: dict) -> dict:
    def get_suffix(win: str, field: str, suffix="PB"):
        window = risk.get(win) or {}
        return number((window.get("risk") or window).get(f"{field}{suffix}"))

    def get_raw(win: str, field: str):
        window = risk.get(win) or {}
        return number((window.get("risk") or window).get(field))

    metrics = {}
    for win in ("Y1", "Y3", "Y5"):
        excess = get_suffix(win, "excess")
        track = get_suffix(win, "track")
        metrics[win] = {
            "return": get_raw(win, "return"),
            "excess": excess,
            "ir": get_suffix(win, "info"),
            "sharpe": get_raw(win, "sharpeRatio"),
            "calmar": get_raw(win, "calmarRatio"),
            "drawdown": get_raw(win, "maxDrawdown"),
            "stddev": get_raw(win, "stdDev"),
            "batting": get_suffix(win, "battingAverage"),
            "beta": get_suffix(win, "beta"),
            "r_squared": get_suffix(win, "rSquared"),
            "track": track,
            "upside_capture": get_suffix(win, "upsideCaptureRatio"),
            "downside_capture": get_suffix(win, "downsideCaptureRatio"),
        }
    return metrics

def fetch_fund_metrics(fund_code: str) -> dict[str, Any] | None:
    payload = http_json(MORNINGSTAR_URL.format(code=fund_code))
    data = payload.get("data") or {}
    risk = data.get("risk") or {}
    windows = extract_risk_metrics(risk) if risk else {
        win: {key: None for key in ("return", "excess", "ir", "sharpe", "calmar", "drawdown", "stddev", "batting", "beta", "r_squared", "track", "upside_capture", "downside_capture")}
        for win in ("Y1", "Y3", "Y5")
    }
    rating = data.get("rating") or {}
    return {
        "risk_date": risk.get("riskDate"),
        "benchmark": data.get("performanceBenchmarkName"),
        "category": data.get("categoryName"),
        "windows": windows,
        "rating_y3": number(rating.get("Y3")),
        "rating_y5": number(rating.get("Y5")),
    }

def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(enhanced_funds)")}
    for column in ("benchmark", "category"):
        if column not in columns:
            connection.execute(f"ALTER TABLE enhanced_funds ADD COLUMN {column} TEXT")
    connection.commit()
    return connection

def refresh(
    connection: sqlite3.Connection,
    workers: int,
    target_indexes: list[tuple[str, str]] = TARGET_INDEXES,
) -> None:
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for index_code, index_name in target_indexes:
        funds = fetch_enhanced_list(index_code)
        print(f"{index_name}({index_code}): {len(funds)} 只指数增强", flush=True)
        metrics_by_code: dict[str, dict[str, Any] | None] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(fetch_fund_metrics, fund["fund_code"]): fund["fund_code"] for fund in funds}
            for future in as_completed(futures):
                fund_code = futures[future]
                try:
                    metrics_by_code[fund_code] = future.result()
                except Exception as error:
                    print(f"  warning: {fund_code} 指标获取失败: {error}", flush=True)
                    metrics_by_code[fund_code] = None

        rows = []
        for fund in funds:
            metrics = metrics_by_code.get(fund["fund_code"])
            if metrics is None:
                windows = {win: {k: None for k in ("return", "excess", "ir", "sharpe", "calmar", "drawdown", "stddev", "batting", "beta", "r_squared", "track", "upside_capture", "downside_capture")} for win in ("Y1", "Y3", "Y5")}
                risk_date = None
                benchmark = category = None
                rating_y3 = rating_y5 = None
            else:
                windows = metrics["windows"]
                risk_date = metrics["risk_date"]
                benchmark = metrics["benchmark"]
                category = metrics["category"]
                rating_y3 = metrics["rating_y3"]
                rating_y5 = metrics["rating_y5"]
            
            rows.append((
                index_code, fund["fund_code"], fund["fund_name"], fund["fund_manager"],
                fund["aum"], fund["inception_date"], risk_date, benchmark, category,
                windows["Y5"]["return"], windows["Y5"]["excess"], windows["Y5"]["ir"], windows["Y5"]["sharpe"], windows["Y5"]["calmar"], windows["Y5"]["drawdown"], windows["Y5"]["stddev"], windows["Y5"]["batting"],
                windows["Y3"]["return"], windows["Y3"]["excess"], windows["Y3"]["ir"], windows["Y3"]["sharpe"],
                windows["Y1"]["excess"],
                windows["Y5"]["beta"], windows["Y5"]["r_squared"], windows["Y5"]["track"], windows["Y5"]["upside_capture"], windows["Y5"]["downside_capture"],
                rating_y3, rating_y5, fetched_at,
            ))
        connection.execute("DELETE FROM enhanced_funds WHERE index_code = ?", (index_code,))
        connection.executemany(
            """
            INSERT INTO enhanced_funds(
                index_code, fund_code, fund_name, fund_manager, aum, inception_date, risk_date, benchmark, category,
                return5, excess5, ir5, sharpe5, calmar5, drawdown5, stddev5, batting5,
                return3, excess3, ir3, sharpe3,
                excess1,
                beta5, r_squared5, track5, upside_capture5, downside_capture5,
                rating_y3, rating_y5, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
        print(f"  写入 {len(rows)} 只", flush=True)

def build_dashboard(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    groups = []
    as_of = None
    for index_code, index_name in TARGET_INDEXES:
        rows = connection.execute("SELECT * FROM enhanced_funds WHERE index_code = ?", (index_code,)).fetchall()
        
        rating_dates = [row["risk_date"] for row in rows if row["risk_date"]]
        if rating_dates:
            reference_date = Counter(rating_dates).most_common(1)[0][0]
            if not as_of or reference_date > as_of:
                as_of = reference_date
            
        eligible = []
        watch = []
        for row in rows:
            record = dict(row)
            reason = None if row["rating_y5"] is not None else "缺少五年晨星评级，转入观察池"
                    
            if not reason:
                eligible.append(record)
            else:
                record["observation_reason"] = reason
                watch.append(record)
                
        def sort_key(f):
            return (
                -(f["rating_y5"] if f["rating_y5"] is not None else -1),
                -(f["rating_y3"] if f["rating_y3"] is not None else -1),
                f["fund_code"],
            )
        eligible.sort(key=sort_key)
        
        def to_frontend(r):
            res = {
                "code": r["fund_code"],
                "name": r["fund_name"],
                "manager": r["fund_manager"],
                "inceptionDate": r["inception_date"],
                "benchmark": r.get("benchmark"),
                "rating": {"y3": r["rating_y3"], "y5": r["rating_y5"]},
                "ir": {"y3": r["ir3"]},
                "excess": {"y1": r["excess1"], "y3": r["excess3"], "y5": r["excess5"]},
                "return5": r["return5"],
                "maxDrawdown5": r["drawdown5"],
            }
            if "observation_reason" in r:
                res["reason"] = r["observation_reason"]
            return res

        groups.append({
            "indexCode": index_code,
            "indexName": index_name,
            "ranked": [to_frontend(r) for r in eligible],
            "watch": [to_frontend(r) for r in watch],
        })
    return {
        "asOf": as_of,
        "source": SOURCE,
        "benchmark": "五年晨星评级优先，三年晨星评级用于同级排序",
        "scoreNote": "排序规则很简单：先按五年晨星评级从高到低排列；五年评级相同时，再按三年晨星评级从高到低排列。缺少五年评级的基金进入观察池。晨星评级是历史风险调整收益的汇总，不等同于买入建议。",
        "groups": groups,
    }

def write_dashboard(connection: sqlite3.Connection) -> None:
    dashboard = build_dashboard(connection)
    OUTPUT_PATH.write_text(
        "window.ENHANCED_FUNDS_DATA = " + json.dumps(dashboard, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    total = sum(len(group["ranked"]) + len(group["watch"]) for group in dashboard["groups"])
    print(f"wrote {OUTPUT_PATH}: {len(dashboard['groups'])} 指数, {total} 只基金, as of {dashboard['asOf']}", flush=True)

def main() -> None:
    parser = argparse.ArgumentParser(description="更新指数增强基金评价数据")
    parser.add_argument("--dashboard-only", action="store_true", help="不联网，仅用现有数据库重建前端数据")
    parser.add_argument("--workers", type=int, default=6, help="晨星指标请求的并发数")
    parser.add_argument("--index-code", choices=[code for code, _ in TARGET_INDEXES], help="仅刷新一个标的指数")
    args = parser.parse_args()
    connection = connect()
    try:
        if not args.dashboard_only:
            selected_indexes = [item for item in TARGET_INDEXES if item[0] == args.index_code] if args.index_code else TARGET_INDEXES
            refresh(connection, args.workers, selected_indexes)
        write_dashboard(connection)
    finally:
        connection.close()

if __name__ == "__main__":
    main()
