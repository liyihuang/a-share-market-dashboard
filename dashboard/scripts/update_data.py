#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = Path(__file__).resolve().parents[1]
UPDATERS = {
    "industry": ROOT / "industry" / "scripts" / "update_data.py",
    "market": ROOT / "market" / "scripts" / "update_data.py",
    "factor": APP_ROOT / "scripts" / "update_factors.py",
}


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="统一更新 A 股市场与申万行业看板")
    parser.add_argument("--scope", choices=["all", "market", "industry", "factor"], default="all")
    parser.add_argument("--full", action="store_true", help="完整回填所选数据源")
    parser.add_argument("--skip-components", action="store_true", help="仅更新行情与估值")
    parser.add_argument("--dashboard-only", action="store_true", help="不联网，只重新生成前端数据")
    args = parser.parse_args()

    scopes = ["market", "industry", "factor"] if args.scope == "all" else [args.scope]
    if not args.dashboard_only:
        for scope in scopes:
            command = [sys.executable, str(UPDATERS[scope])]
            if args.full:
                command.append("--full")
            if args.skip_components and scope in {"market", "industry"}:
                command.append("--skip-components")
            run(command)

    run([sys.executable, str(APP_ROOT / "scripts" / "build_dashboard.py")])


if __name__ == "__main__":
    main()
