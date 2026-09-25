#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import ssl
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://www.swsresearch.com/institute-sw/api/index_analysis/index_analysis_report/"
COMPONENT_API_URL = "https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/"
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "industry-history.sqlite3"
OUTPUT_PATH = DATA_DIR / "dashboard-data.js"
SOURCE = "申万宏源指数分析"

# This is display metadata, not the source of truth. New codes returned by the
# provider are discovered automatically and use the provider's current name.
DISPLAY_ORDER = [
    "801010", "801030", "801040", "801050", "801080", "801880", "801110",
    "801120", "801130", "801140", "801150", "801160", "801170", "801180",
    "801200", "801210", "801780", "801790", "801230", "801710", "801720",
    "801730", "801890", "801740", "801750", "801760", "801770", "801950",
    "801960", "801970", "801980",
]

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS index_series (
    code TEXT PRIMARY KEY,
    current_name TEXT NOT NULL,
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_names (
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    PRIMARY KEY (code, valid_from),
    FOREIGN KEY (code) REFERENCES index_series(code)
);

CREATE TABLE IF NOT EXISTS classification_versions (
    version_id TEXT PRIMARY KEY,
    version_name TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL CHECK (status IN ('reviewed', 'pending')),
    note TEXT
);

CREATE TABLE IF NOT EXISTS index_identities (
    identity_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    observed_from TEXT NOT NULL,
    observed_to TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    version_id TEXT,
    note TEXT,
    FOREIGN KEY (code) REFERENCES index_series(code),
    FOREIGN KEY (version_id) REFERENCES classification_versions(version_id)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name_as_published TEXT NOT NULL,
    close REAL,
    volume REAL,
    change_pct REAL,
    turnover_rate REAL,
    pe REAL,
    pb REAL,
    mean_price REAL,
    amount_share REAL,
    float_market_cap REAL,
    average_float_market_cap REAL,
    dividend_yield REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, code),
    FOREIGN KEY (code) REFERENCES index_series(code)
);

CREATE INDEX IF NOT EXISTS idx_daily_metrics_code_date
ON daily_metrics(code, trade_date);

CREATE TABLE IF NOT EXISTS current_index_components (
    code TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    weight REAL,
    included_date TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, stock_code),
    FOREIGN KEY (code) REFERENCES index_series(code)
);

CREATE INDEX IF NOT EXISTS idx_current_index_components_code_weight
ON current_index_components(code, weight DESC);

CREATE TABLE IF NOT EXISTS index_lineage (
    predecessor_code TEXT NOT NULL,
    successor_code TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('rename', 'split', 'merge', 'new', 'reclassify')),
    effective_date TEXT NOT NULL,
    directly_comparable INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    PRIMARY KEY (predecessor_code, successor_code, effective_date)
);

CREATE TABLE IF NOT EXISTS classification_events (
    effective_date TEXT PRIMARY KEY,
    previous_signature TEXT,
    new_signature TEXT NOT NULL,
    previous_count INTEGER,
    new_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('observed', 'reviewed', 'ignored')),
    details TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
"""

KNOWN_VERSIONS = [
    (
        "legacy_16",
        "申万早期一级行业",
        "1999-12-30",
        "2014-02-20",
        "reviewed",
        "公开接口中包含 16 个一级行业。",
    ),
    (
        "sw_2014",
        "申万行业分类 2014",
        "2014-02-21",
        "2021-12-12",
        "reviewed",
        "一级行业扩展至 28 个，后期部分指数停止发布。",
    ),
    (
        "sw_2021",
        "申万行业分类 2021",
        "2021-12-13",
        None,
        "reviewed",
        "现行 31 个一级行业口径。",
    ),
]

# These dates are visible in the complete provider history and have been
# inspected. They include classification expansion, stable renames, and a
# discontinued implementation series.
KNOWN_REVIEWED_EVENT_DATES = {
    "1999-12-30",
    "2011-09-30",
    "2014-02-21",
    "2015-01-22",
    "2017-01-23",
    "2021-12-13",
}


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def parse_trade_date(raw: dict[str, Any]) -> date | None:
    try:
        return datetime.strptime(str(raw["bargaindate"])[:10], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return None


def fetch_period(start: str, end: str, attempts: int = 4, timeout: int = 20) -> list[dict[str, Any]]:
    params = {
        "page": 1,
        "page_size": 10000,
        "index_type": "一级行业",
        "start_date": start,
        "end_date": end,
        "type": "DAY",
        "swindexcode": "all",
    }
    context = ssl._create_unverified_context()
    for attempt in range(attempts):
        try:
            request = Request(
                f"{API_URL}?{urlencode(params)}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urlopen(request, timeout=timeout, context=context) as response:
                payload = json.load(response)
            data = payload["data"]
            rows = data.get("results", [])
            expected = int(data.get("count", 0))
            if expected > len(rows):
                raise RuntimeError(f"response truncated: {len(rows)}/{expected}")
            return rows
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    return []


def fetch_history(start_date: date, end_date: date, workers: int) -> list[dict[str, Any]]:
    periods: list[tuple[str, str]] = []
    for year in range(start_date.year, end_date.year + 1):
        period_start = max(start_date, date(year, 1, 1))
        period_end = min(end_date, date(year, 12, 31))
        periods.append((period_start.isoformat(), period_end.isoformat()))

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_period, start, end): (start, end) for start, end in periods}
        try:
            for future in as_completed(futures):
                start, end = futures[future]
                batch = future.result()
                print(f"fetched {start}..{end}: {len(batch)} rows", flush=True)
                rows.extend(batch)
        except Exception:
            # If the provider is unreachable, every other in-flight/queued
            # period will fail the same way after its own retries. Bail out
            # immediately instead of burning the job's time budget serially
            # re-discovering that fact for each remaining period.
            executor.shutdown(cancel_futures=True)
            raise
    return rows


def fetch_components(code: str, attempts: int = 4, timeout: int = 20) -> list[dict[str, Any]]:
    params = {"swindexcode": code, "page": 1, "page_size": 10000}
    context = ssl._create_unverified_context()
    for attempt in range(attempts):
        try:
            request = Request(
                f"{COMPONENT_API_URL}?{urlencode(params)}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urlopen(request, timeout=timeout, context=context) as response:
                payload = json.load(response)
            return payload["data"]["results"]
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    return []


def refresh_components(connection: sqlite3.Connection, codes: list[str], workers: int) -> None:
    """Replace a code's component set only after its full latest response succeeds."""
    if not codes:
        return
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    responses: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_components, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                responses[code] = future.result()
                print(f"fetched components {code}: {len(responses[code])} stocks", flush=True)
            except Exception as error:
                # Keep the last successful snapshot when the upstream component
                # endpoint is temporarily unavailable for one industry.
                print(f"warning: could not refresh components {code}: {error}", flush=True)

    for code, raw_components in responses.items():
        components = []
        for raw in raw_components:
            stock_code = str(raw.get("stockcode", "")).strip()
            stock_name = str(raw.get("stockname", "")).strip()
            if not stock_code or not stock_name:
                continue
            included_raw = str(raw.get("beginningdate", "")).strip()
            components.append((
                code,
                stock_code,
                stock_name,
                number(raw.get("newweight")),
                included_raw[:10] if included_raw else None,
                fetched_at,
            ))
        if not components:
            print(f"warning: component response for {code} had no usable rows; keeping prior snapshot", flush=True)
            continue
        connection.execute("DELETE FROM current_index_components WHERE code = ?", (code,))
        connection.executemany(
            """
            INSERT INTO current_index_components
                (code, stock_code, stock_name, weight, included_date, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            components,
        )


def connect_database() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    connection.executemany(
        """
        INSERT INTO classification_versions
            (version_id, version_name, effective_from, effective_to, status, note)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(version_id) DO UPDATE SET
            version_name = excluded.version_name,
            effective_from = excluded.effective_from,
            effective_to = excluded.effective_to,
            status = excluded.status,
            note = excluded.note
        """,
        KNOWN_VERSIONS,
    )
    connection.commit()
    return connection


def normalized_rows(raw_rows: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    rows = []
    for raw in raw_rows:
        trade_date = parse_trade_date(raw)
        code = str(raw.get("swindexcode", "")).strip()
        name = str(raw.get("swindexname", "")).strip()
        if trade_date is None or not code or not name:
            continue
        rows.append({
            "trade_date": trade_date.isoformat(),
            "code": code,
            "name": name,
            "close": number(raw.get("closeindex")),
            "volume": number(raw.get("bargainamount")),
            "change_pct": number(raw.get("markup")),
            "turnover_rate": number(raw.get("turnoverrate")),
            "pe": number(raw.get("pe")),
            "pb": number(raw.get("pb")),
            "mean_price": number(raw.get("meanprice")),
            "amount_share": number(raw.get("bargainsumrate")),
            "float_market_cap": number(raw.get("negotiablessharesum1")),
            "average_float_market_cap": number(raw.get("negotiablessharesum2")),
            "dividend_yield": number(raw.get("dp")),
            "fetched_at": fetched_at,
        })
    return rows


def upsert_rows(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_code[row["code"]].append(row)

    for code, code_rows in by_code.items():
        code_rows.sort(key=lambda row: row["trade_date"])
        first_date = code_rows[0]["trade_date"]
        last_date = code_rows[-1]["trade_date"]
        current_name = code_rows[-1]["name"]
        connection.execute(
            """
            INSERT INTO index_series
                (code, current_name, first_seen_date, last_seen_date, active, discovered_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                current_name = excluded.current_name,
                first_seen_date = min(index_series.first_seen_date, excluded.first_seen_date),
                last_seen_date = max(index_series.last_seen_date, excluded.last_seen_date),
                updated_at = excluded.updated_at
            """,
            (code, current_name, first_date, last_date, now, now),
        )

    connection.executemany(
        """
        INSERT INTO daily_metrics (
            trade_date, code, name_as_published, close, volume, change_pct,
            turnover_rate, pe, pb, mean_price, amount_share, float_market_cap,
            average_float_market_cap, dividend_yield, source, fetched_at
        ) VALUES (
            :trade_date, :code, :name, :close, :volume, :change_pct,
            :turnover_rate, :pe, :pb, :mean_price, :amount_share, :float_market_cap,
            :average_float_market_cap, :dividend_yield, :source, :fetched_at
        )
        ON CONFLICT(trade_date, code) DO UPDATE SET
            name_as_published = excluded.name_as_published,
            close = excluded.close,
            volume = excluded.volume,
            change_pct = excluded.change_pct,
            turnover_rate = excluded.turnover_rate,
            pe = excluded.pe,
            pb = excluded.pb,
            mean_price = excluded.mean_price,
            amount_share = excluded.amount_share,
            float_market_cap = excluded.float_market_cap,
            average_float_market_cap = excluded.average_float_market_cap,
            dividend_yield = excluded.dividend_yield,
            source = excluded.source,
            fetched_at = excluded.fetched_at
        """,
        [{**row, "source": SOURCE} for row in rows],
    )


def classification_version_for(connection: sqlite3.Connection, observed_from: str) -> str | None:
    row = connection.execute(
        """
        SELECT version_id FROM classification_versions
        WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (observed_from, observed_from),
    ).fetchone()
    return row[0] if row else None


def rebuild_index_identities(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM index_names")
    connection.execute("DELETE FROM index_identities")
    codes = [row[0] for row in connection.execute("SELECT code FROM index_series ORDER BY code")]
    for code in codes:
        observations = connection.execute(
            "SELECT trade_date, name_as_published FROM daily_metrics WHERE code = ? ORDER BY trade_date",
            (code,),
        ).fetchall()
        if not observations:
            continue

        periods: list[tuple[str, str, str]] = []
        period_name = observations[0][1]
        period_start = observations[0][0]
        period_end = observations[0][0]
        for trade_date, name in observations[1:]:
            gap = date.fromisoformat(trade_date) - date.fromisoformat(period_end)
            if name != period_name or gap.days > 45:
                periods.append((period_name, period_start, period_end))
                period_name = name
                period_start = trade_date
            period_end = trade_date
        periods.append((period_name, period_start, period_end))

        for period_index, (name, observed_from, observed_to) in enumerate(periods):
            identity_id = f"{code}@{observed_from}"
            # The last period is this code's current identity, regardless of
            # whether *other* codes also reported on its most recent date.
            # A provider glitch that drops a handful of codes for one day
            # must not make those codes look like they have no active
            # identity (see refresh_series_metadata's partial-day handling).
            active = int(period_index == len(periods) - 1)
            version_id = classification_version_for(connection, observed_from)
            connection.execute(
                "INSERT INTO index_names(code, name, valid_from, valid_to) VALUES (?, ?, ?, ?)",
                (code, name, observed_from, observed_to if not active else None),
            )
            connection.execute(
                """
                INSERT INTO index_identities
                    (identity_id, code, name, observed_from, observed_to, active, version_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (identity_id, code, name, observed_from, observed_to, active, version_id),
            )


def rebuild_classification_events(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM classification_events WHERE status = 'observed'")
    observations = connection.execute(
        "SELECT trade_date, code, name_as_published FROM daily_metrics ORDER BY trade_date, code"
    ).fetchall()
    by_date: dict[str, list[str]] = defaultdict(list)
    for trade_date, code, name in observations:
        by_date[trade_date].append(f"{code}:{name}")

    runs: list[dict[str, Any]] = []
    for trade_date, members in by_date.items():
        signature = "|".join(sorted(members))
        if not runs or runs[-1]["signature"] != signature:
            runs.append({
                "start": trade_date,
                "end": trade_date,
                "signature": signature,
                "count": len(members),
                "days": 1,
            })
        else:
            runs[-1]["end"] = trade_date
            runs[-1]["days"] += 1

    qualifying_runs = [run for run in runs if run["days"] >= 3 or run["start"] == min(by_date)]
    stable_runs: list[dict[str, Any]] = []
    for run in qualifying_runs:
        # A one- or two-day missing series is a provider anomaly, not a new
        # classification. Merge the surrounding identical stable signatures.
        if stable_runs and stable_runs[-1]["signature"] == run["signature"]:
            stable_runs[-1]["end"] = run["end"]
            stable_runs[-1]["days"] += run["days"]
        else:
            stable_runs.append(dict(run))
    for index, run in enumerate(stable_runs):
        previous = stable_runs[index - 1] if index else None
        known_version = connection.execute(
            "SELECT 1 FROM classification_versions WHERE effective_from = ? AND status = 'reviewed'",
            (run["start"],),
        ).fetchone()
        status = "reviewed" if known_version or run["start"] in KNOWN_REVIEWED_EVENT_DATES else "observed"
        details = json.dumps({
            "stable_trading_days": run["days"],
            "observed_to": run["end"],
        }, ensure_ascii=False)
        connection.execute(
            """
            INSERT INTO classification_events (
                effective_date, previous_signature, new_signature,
                previous_count, new_count, status, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(effective_date) DO UPDATE SET
                previous_signature = excluded.previous_signature,
                new_signature = excluded.new_signature,
                previous_count = excluded.previous_count,
                new_count = excluded.new_count,
                status = CASE
                    WHEN classification_events.status IN ('reviewed', 'ignored')
                    THEN classification_events.status ELSE excluded.status END,
                details = excluded.details
            """,
            (
                run["start"],
                previous["signature"] if previous else None,
                run["signature"],
                previous["count"] if previous else None,
                run["count"],
                status,
                details,
            ),
        )


def refresh_series_metadata(connection: sqlite3.Connection) -> None:
    latest_date = connection.execute("SELECT max(trade_date) FROM daily_metrics").fetchone()[0]
    if not latest_date:
        return
    active_codes = {
        row[0] for row in connection.execute(
            "SELECT DISTINCT code FROM daily_metrics WHERE trade_date = ?", (latest_date,)
        )
    }
    recent_counts = [
        row[0] for row in connection.execute(
            """
            SELECT count(*) FROM daily_metrics
            WHERE trade_date < ? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5
            """,
            (latest_date,),
        )
    ]
    expected_count = max(recent_counts) if recent_counts else len(active_codes)
    if len(active_codes) >= expected_count:
        connection.execute("UPDATE index_series SET active = 0")
        connection.executemany(
            "UPDATE index_series SET active = 1 WHERE code = ?",
            [(code,) for code in active_codes],
        )
    else:
        print(
            f"warning: latest date {latest_date} has {len(active_codes)} series; "
            f"keeping prior active set of {expected_count}",
            flush=True,
        )
    connection.execute(
        """
        UPDATE index_series SET
            current_name = (
                SELECT name_as_published FROM daily_metrics d
                WHERE d.code = index_series.code ORDER BY trade_date DESC LIMIT 1
            ),
            first_seen_date = (SELECT min(trade_date) FROM daily_metrics d WHERE d.code = index_series.code),
            last_seen_date = (SELECT max(trade_date) FROM daily_metrics d WHERE d.code = index_series.code)
        """
    )
    rebuild_index_identities(connection)
    rebuild_classification_events(connection)


def percentile(values: list[float], current: float | None) -> float | None:
    if current is None or not values:
        return None
    return sum(value <= current for value in values) / len(values)


def price_on_or_before(rows: list[dict[str, Any]], target: date) -> float | None:
    eligible = [row for row in rows if row["date"] <= target and row["close"] is not None]
    return eligible[-1]["close"] if eligible else None


def period_return(rows: list[dict[str, Any]], current_close: float | None, target: date) -> float | None:
    start_close = price_on_or_before(rows, target)
    if current_close is None or not start_close:
        return None
    return current_close / start_close - 1


def shift_year(value: date, years: int) -> date:
    year = value.year + years
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return date(year, value.month, day)


def methodology_record(
    code: str,
    current_name: str,
    identities: list[sqlite3.Row],
    history_start: str,
    comparable_start: str,
) -> dict[str, Any]:
    events: list[dict[str, str]] = []
    has_break = False
    previous = None
    for identity_index, identity in enumerate(identities):
        observed_from = identity["observed_from"]
        observed_to = identity["observed_to"]
        name = identity["name"]
        if previous is None:
            label = "数据首日"
            description = f"公开接口最早记录为“{name}”。"
            if observed_from > "1999-12-30":
                label = "公开记录开始"
                description = f"公开接口从该日开始记录“{name}”。"
            if len(identities) > 1:
                next_identity = identities[identity_index + 1]
                gap_days = (
                    date.fromisoformat(next_identity["observed_from"])
                    - date.fromisoformat(observed_to)
                ).days
                if gap_days > 45:
                    label = "旧代码记录"
                    description = (
                        f"代码 {code} 当时对应“{name}”，不是当前的“{current_name}”。"
                    )
        else:
            gap_days = (
                date.fromisoformat(observed_from) - date.fromisoformat(previous["observed_to"])
            ).days
            if gap_days > 45:
                label = "当前序列开始"
                description = (
                    f"代码 {code} 在中断 {gap_days} 天后开始发布“{name}”；"
                    f"更早的“{previous['name']}”不是当前指数，不参与收益和估值计算。"
                )
                has_break = True
            else:
                label = "名称调整"
                description = f"指数名称由“{previous['name']}”调整为“{name}”；官方点位连续。"
        events.append({"date": observed_from, "label": label, "description": description})
        previous = identity

    if has_break:
        status = "broken"
        status_label = "旧记录不适用"
        summary = (
            f"当前“{current_name}”的收益、PE 和 PB 只使用 {comparable_start} 之后的数据。"
            "同代码更早的记录属于其他指数，只在原始记录中保留。"
        )
    elif len(identities) > 1:
        status = "changed"
        status_label = "有调整"
        summary = "发现名称或分类表达调整；未发现代码复用，官方点位保持连续。"
    else:
        status = "stable"
        status_label = "未见大改"
        summary = (
            f"自 {history_start} 以来未发现代码复用、名称变更或长期中断；"
            "常规成分股调整仍会发生。"
        )

    if not any(event["date"] == "2021-12-13" for event in events):
        events.append({
            "date": "2021-12-13",
            "label": "现行分类",
            "description": (
                f"当前“{current_name}”按申万 2021 行业分类展示；"
                f"收益和长期估值分位从 {comparable_start} 起计算。"
            ),
        })
    events.sort(key=lambda event: event["date"])
    return {"status": status, "statusLabel": status_label, "summary": summary, "events": events}


def build_dashboard(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    series = connection.execute(
        "SELECT code, current_name, first_seen_date FROM index_series WHERE active = 1"
    ).fetchall()
    order = {code: index for index, code in enumerate(DISPLAY_ORDER)}
    series = sorted(series, key=lambda row: (order.get(row["code"], 9999), row["code"]))

    latest_date = date.fromisoformat(
        connection.execute("SELECT max(trade_date) FROM daily_metrics").fetchone()[0]
    )
    version = connection.execute(
        """
        SELECT version_id, version_name, effective_from
        FROM classification_versions
        WHERE status = 'reviewed' AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (latest_date.isoformat(), latest_date.isoformat()),
    ).fetchone()
    if version is None:
        raise RuntimeError(f"no reviewed classification version for {latest_date}")
    version_id, version_name, version_start = version
    latest_event = connection.execute(
        """
        SELECT effective_date, status FROM classification_events
        WHERE effective_date <= ? ORDER BY effective_date DESC LIMIT 1
        """,
        (latest_date.isoformat(),),
    ).fetchone()
    industries = []
    for item in series:
        db_rows = connection.execute(
            """
            SELECT trade_date, close, pe, pb, dividend_yield
            FROM daily_metrics WHERE code = ? ORDER BY trade_date
            """,
            (item["code"],),
        ).fetchall()
        rows = [
            {
                "date": date.fromisoformat(row["trade_date"]),
                "close": row["close"],
                "pe": row["pe"],
                "pb": row["pb"],
                "dividend_yield": row["dividend_yield"],
            }
            for row in db_rows
        ]
        current = rows[-1]

        month_end: dict[str, dict[str, Any]] = {}
        all_month_end: dict[str, dict[str, Any]] = {}
        identities = connection.execute(
            """
            SELECT identity_id, name, observed_from, observed_to, active FROM index_identities
            WHERE code = ? ORDER BY observed_from
            """,
            (item["code"],),
        ).fetchall()
        active_identity = next((identity for identity in identities if identity["active"]), None)
        if active_identity is None:
            raise RuntimeError(f"no active identity for {item['code']}")
        identity_before_active = None
        for identity in identities:
            if identity["identity_id"] == active_identity["identity_id"]:
                break
            identity_before_active = identity
        has_identity_break = False
        if identity_before_active is not None:
            gap_days = (
                date.fromisoformat(active_identity["observed_from"])
                - date.fromisoformat(identity_before_active["observed_to"])
            ).days
            has_identity_break = gap_days > 45
        comparable_start = (
            active_identity["observed_from"] if has_identity_break else identities[0]["observed_from"]
        )
        comparable_rows = [row for row in rows if row["date"].isoformat() >= comparable_start]
        pe_values = [row["pe"] for row in comparable_rows if row["pe"] is not None and row["pe"] > 0]
        pb_values = [row["pb"] for row in comparable_rows if row["pb"] is not None and row["pb"] > 0]
        for row in rows:
            all_month_end[row["date"].strftime("%Y-%m")] = row
        for row in comparable_rows:
            month_end[row["date"].strftime("%Y-%m")] = row
        history = [
            {"date": row["date"].isoformat(), "close": row["close"], "pe": row["pe"], "pb": row["pb"]}
            for row in month_end.values()
        ]
        history_all = []
        for row in all_month_end.values():
            row_date = row["date"].isoformat()
            identity = next(
                (
                    identity_row["identity_id"] for identity_row in identities
                    if identity_row["observed_from"] <= row_date <= identity_row["observed_to"]
                ),
                f"{item['code']}@unknown",
            )
            history_all.append({
                "date": row_date,
                "close": row["close"],
                "pe": row["pe"],
                "pb": row["pb"],
                "identity": identity,
                "identityName": next(
                    (
                        identity_row["name"] for identity_row in identities
                        if identity_row["identity_id"] == identity
                    ),
                    item["current_name"],
                ),
            })

        components = connection.execute(
            """
            SELECT stock_code, stock_name, weight, included_date
            FROM current_index_components
            WHERE code = ?
            ORDER BY weight DESC, stock_code
            """,
            (item["code"],),
        ).fetchall()
        component_fetched_at = connection.execute(
            "SELECT max(fetched_at) FROM current_index_components WHERE code = ?",
            (item["code"],),
        ).fetchone()[0]

        industries.append({
            "code": item["code"],
            "name": item["current_name"],
            "coverageStart": item["first_seen_date"],
            "comparableStart": comparable_start,
            "close": current["close"],
            "pe": current["pe"],
            "pb": current["pb"],
            "dividendYield": current["dividend_yield"],
            "pePercentile": percentile(pe_values, current["pe"]),
            "pbPercentile": percentile(pb_values, current["pb"]),
            "returnYtd": period_return(comparable_rows, current["close"], date(latest_date.year - 1, 12, 31)),
            "return1y": period_return(comparable_rows, current["close"], shift_year(latest_date, -1)),
            "return3y": period_return(comparable_rows, current["close"], shift_year(latest_date, -3)),
            "return5y": period_return(comparable_rows, current["close"], shift_year(latest_date, -5)),
            "history": history,
            "historyAll": history_all,
            "components": [
                {
                    "code": component["stock_code"],
                    "name": component["stock_name"],
                    "weight": component["weight"],
                    "includedDate": component["included_date"],
                }
                for component in components
            ],
            "componentsFetchedAt": component_fetched_at,
            "methodology": methodology_record(
                item["code"], item["current_name"], identities,
                item["first_seen_date"], comparable_start
            ),
        })

    history_start = connection.execute("SELECT min(trade_date) FROM daily_metrics").fetchone()[0]
    return {
        "asOf": latest_date.isoformat(),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE,
        "classificationVersion": {
            "id": version_id,
            "name": version_name,
            "effectiveFrom": version_start,
            "status": latest_event[1] if latest_event else "observed",
            "latestObservedChange": latest_event[0] if latest_event else None,
        },
        "historyStart": history_start,
        "industries": industries,
    }


def write_dashboard(connection: sqlite3.Connection) -> None:
    dashboard = build_dashboard(connection)
    content = "window.INDUSTRY_DASHBOARD_DATA = " + json.dumps(
        dashboard, ensure_ascii=False, separators=(",", ":")
    ) + ";\n"
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(
        f"wrote {OUTPUT_PATH}: {len(dashboard['industries'])} active industries "
        f"as of {dashboard['asOf']}",
        flush=True,
    )


def choose_start_date(connection: sqlite3.Connection, full: bool) -> date:
    if full:
        return date(1999, 12, 30)
    latest = connection.execute("SELECT max(trade_date) FROM daily_metrics").fetchone()[0]
    return date.fromisoformat(latest) - timedelta(days=14) if latest else date(1999, 12, 30)


def main() -> None:
    parser = argparse.ArgumentParser(description="更新 A 股行业估值数据库和网页数据")
    parser.add_argument("--full", action="store_true", help="从 1999-12-30 完整回填")
    parser.add_argument("--start-date", help="覆盖自动起点，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--component-workers", type=int, default=4)
    parser.add_argument("--component-codes", nargs="+", help="只刷新指定行业代码的成分股")
    parser.add_argument("--components-only", action="store_true", help="只刷新成分股并重新生成网页数据")
    parser.add_argument("--skip-components", action="store_true", help="跳过本次成分股刷新")
    parser.add_argument("--dashboard-only", action="store_true", help="只从数据库重新生成网页数据")
    args = parser.parse_args()

    connection = connect_database()
    if args.dashboard_only:
        with connection:
            refresh_series_metadata(connection)
        write_dashboard(connection)
        connection.close()
        return

    if args.components_only:
        with connection:
            refresh_series_metadata(connection)
            active_codes = [row[0] for row in connection.execute(
                "SELECT code FROM index_series WHERE active = 1 ORDER BY code"
            )]
            refresh_components(
                connection,
                args.component_codes or active_codes,
                args.component_workers,
            )
        write_dashboard(connection)
        connection.close()
        return

    start_date = date.fromisoformat(args.start_date) if args.start_date else choose_start_date(connection, args.full)
    end_date = date.fromisoformat(args.end_date)
    if start_date > end_date:
        raise SystemExit(f"start date {start_date} is after end date {end_date}")

    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    cursor = connection.execute(
        "INSERT INTO ingestion_runs(started_at, start_date, end_date, status) VALUES (?, ?, ?, 'running')",
        (started_at, start_date.isoformat(), end_date.isoformat()),
    )
    run_id = cursor.lastrowid
    connection.commit()

    try:
        raw_rows = fetch_history(start_date, end_date, max(1, args.workers))
        rows = normalized_rows(raw_rows, datetime.now().astimezone().isoformat(timespec="seconds"))
        with connection:
            upsert_rows(connection, rows)
            refresh_series_metadata(connection)
            connection.execute(
                "UPDATE ingestion_runs SET finished_at = ?, status = 'success', row_count = ? WHERE id = ?",
                (datetime.now().astimezone().isoformat(timespec="seconds"), len(rows), run_id),
            )
        if not args.skip_components:
            active_codes = [row[0] for row in connection.execute(
                "SELECT code FROM index_series WHERE active = 1 ORDER BY code"
            )]
            with connection:
                refresh_components(
                    connection,
                    args.component_codes or active_codes,
                    args.component_workers,
                )
        write_dashboard(connection)
        pending_events = connection.execute(
            "SELECT effective_date, previous_count, new_count FROM classification_events WHERE status = 'observed' ORDER BY effective_date"
        ).fetchall()
        if pending_events:
            print("classification changes requiring review:", flush=True)
            for effective_date, previous_count, new_count in pending_events:
                print(f"  {effective_date}: {previous_count or '-'} -> {new_count} industries", flush=True)
    except Exception as error:
        connection.execute(
            "UPDATE ingestion_runs SET finished_at = ?, status = 'failed', error_message = ? WHERE id = ?",
            (datetime.now().astimezone().isoformat(timespec="seconds"), str(error), run_id),
        )
        connection.commit()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
