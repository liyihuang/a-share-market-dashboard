#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = APP_ROOT / "data" / "dashboard.json"
FACTOR_DATA = APP_ROOT / "data" / "factor-data.json"
SOURCES = {
    "market": (
        ROOT / "market" / "data" / "dashboard-data.js",
        "window.CSI_MARKET_DATA = ",
    ),
    "industry": (
        ROOT / "industry" / "data" / "dashboard-data.js",
        "window.INDUSTRY_DASHBOARD_DATA = ",
    ),
}


def read_generated_json(path: Path, prefix: str) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8").strip()
    if not content.startswith(prefix) or not content.endswith(";"):
        raise RuntimeError(f"unexpected generated data format: {path}")
    return json.loads(content[len(prefix):-1])


def load_sector_mapping() -> dict[str, str]:
    """Map each current stock code to its current Shenwan primary industry."""
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


def load_sector_allocations(mapping: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    database = ROOT / "market" / "data" / "csi-market-history.sqlite3"
    if not database.exists() or not mapping:
        return {}
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT code, stock_code, weight FROM current_components WHERE weight IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, dict[str, float]] = {}
    for index_code, stock_code, weight in rows:
        sector = mapping.get(str(stock_code).zfill(6), "未分类")
        sectors = grouped.setdefault(index_code, {})
        sectors[sector] = sectors.get(sector, 0) + float(weight)
    allocations: dict[str, list[dict[str, Any]]] = {}
    for index_code, sectors in grouped.items():
        ordered = sorted(sectors.items(), key=lambda pair: pair[1], reverse=True)
        allocations[index_code] = [{"name": name, "weight": round(weight, 4)} for name, weight in ordered]
    return allocations


def normalize_market(raw: dict[str, Any], allocations: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    items = []
    for item in raw["indexes"]:
        components = [
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "weight": row.get("weight"),
                "date": row.get("date"),
            }
            for row in item.get("components", [])[:10]
        ]
        items.append({
            "code": item["code"],
            "name": item["name"],
            "subtitle": item.get("role"),
            "coverageStart": item.get("coverageStart"),
            "comparableStart": item.get("coverageStart"),
            "close": item.get("close"),
            "changePct": item.get("changePct"),
            "pe": item.get("pe"),
            "pb": None,
            "dividendYield": None,
            "pePercentile": item.get("pePercentile"),
            "pbPercentile": None,
            "returnYtd": item.get("returnYtd"),
            "return1y": item.get("return1y"),
            "return3y": item.get("return3y"),
            "return5y": item.get("return5y"),
            "history": item.get("history", []),
            "historyAll": item.get("history", []),
            "components": components,
            "componentDate": item.get("componentDate"),
            "sectorAllocation": allocations.get(item["code"], []),
            "methodology": {
                "status": "official",
                "statusLabel": "中证官方口径",
                "summary": item.get("methodology", ""),
                "events": [{
                    "date": item.get("coverageStart"),
                    "label": "历史计算起点",
                    "description": "中证公开历史从该日开始；可能包含指数发布前的官方回溯计算。",
                }],
            },
        })
    return {
        "id": "market",
        "label": "市场结构",
        "description": "用中证价格指数观察全市场、均衡宽基与市值层级，并补充深交所创业板指。",
        "source": raw.get("source"),
        "asOf": raw.get("asOf"),
        "metrics": ["return1y", "return3y", "return5y", "pePercentile"],
        "items": items,
    }


def normalize_industry(raw: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in raw["industries"]:
        components = [
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "weight": row.get("weight"),
                "date": row.get("includedDate"),
            }
            for row in item.get("components", [])[:10]
        ]
        items.append({
            "code": item["code"],
            "name": item["name"],
            "subtitle": "申万一级行业",
            "coverageStart": item.get("coverageStart"),
            "comparableStart": item.get("comparableStart"),
            "close": item.get("close"),
            "changePct": None,
            "pe": item.get("pe"),
            "pb": item.get("pb"),
            "dividendYield": item.get("dividendYield"),
            "pePercentile": item.get("pePercentile"),
            "pbPercentile": item.get("pbPercentile"),
            "returnYtd": item.get("returnYtd"),
            "return1y": item.get("return1y"),
            "return3y": item.get("return3y"),
            "return5y": item.get("return5y"),
            "history": item.get("history", []),
            "historyAll": item.get("historyAll", item.get("history", [])),
            "components": components,
            "componentDate": (item.get("componentsFetchedAt") or "")[:10] or None,
            "methodology": item.get("methodology", {}),
        })
    version = raw.get("classificationVersion", {})
    return {
        "id": "industry",
        "label": "申万行业",
        "description": f"{version.get('name', '申万一级行业')}，估值分位按各指数全部可比历史计算。",
        "source": raw.get("source"),
        "asOf": raw.get("asOf"),
        "metrics": ["return1y", "return3y", "return5y", "pePercentile", "pbPercentile"],
        "items": items,
    }


def build() -> dict[str, Any]:
    market_path, market_prefix = SOURCES["market"]
    industry_path, industry_prefix = SOURCES["industry"]
    sector_mapping = load_sector_mapping()
    market = normalize_market(read_generated_json(market_path, market_prefix), load_sector_allocations(sector_mapping))
    industry = normalize_industry(read_generated_json(industry_path, industry_prefix))
    factors = json.loads(FACTOR_DATA.read_text(encoding="utf-8")) if FACTOR_DATA.exists() else None
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "views": [market, industry],
        "factors": factors,
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    dashboard = build()
    OUTPUT.write_text(
        json.dumps(dashboard, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    counts = ", ".join(f"{view['label']} {len(view['items'])}" for view in dashboard["views"])
    if dashboard["factors"]:
        counts += f", 因子表现 {len(dashboard['factors']['factors'])}"
    print(f"wrote {OUTPUT}: {counts}")


if __name__ == "__main__":
    main()
