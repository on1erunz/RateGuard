"""Run Ctrip collection according to the configured monitoring calendar."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from src.notify import send_alert


def _anchor_dates(today: date) -> list[date]:
    """Tomorrow, this Friday, next Monday, and next Friday (deduplicated)."""
    this_friday = today + timedelta(days=(4 - today.weekday()) % 7)
    next_monday = today + timedelta(days=7 - today.weekday())
    next_friday = today + timedelta(days=((4 - today.weekday()) % 7) + 7)
    values = [today + timedelta(days=1), this_friday, next_monday, next_friday]
    return list(dict.fromkeys(values))


def _run(checkin: date, dry_run: bool, summary_path: Path) -> tuple[int, dict[str, int | str]]:
    command = [
        sys.executable, "-m", "src.ctrip_mvp", "--checkin", checkin.isoformat(),
        "--summary-file", str(summary_path),
    ]
    print(" ".join(command))
    if dry_run:
        return 0, {"checkin": checkin.isoformat(), "target_count": 5, "observation_count": 0, "price_alert_count": 0, "error_count": 0}
    completed = subprocess.run(command, check=False)
    summary: dict[str, int | str] = {"checkin": checkin.isoformat(), "target_count": 0, "observation_count": 0, "price_alert_count": 0, "error_count": 1}
    if summary_path.exists():
        try:
            summary.update(json.loads(summary_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Unable to read collection summary: {exc}")
    return completed.returncode, summary


def _send_cycle_status(mode: str, now: datetime, summaries: list[dict[str, int | str]], exit_code: int, dry_run: bool) -> None:
    dates = "、".join(str(item["checkin"]) for item in summaries)
    targets = sum(int(item.get("target_count", 0)) for item in summaries)
    observations = sum(int(item.get("observation_count", 0)) for item in summaries)
    price_alerts = sum(int(item.get("price_alert_count", 0)) for item in summaries)
    errors = sum(int(item.get("error_count", 0)) for item in summaries)
    cycle_name = "当天价格" if mode == "hourly" else "远期锚点"

    if exit_code != 0 or errors:
        title = "[携程采集异常]"
        body = f"{cycle_name}采集结束，但存在异常。\n日期：{dates}\n酒店任务：{targets}，记录：{observations}，异常：{errors}"
    elif price_alerts == 0:
        title = "[携程采集完成] 无价格变动"
        body = f"{cycle_name}采集完成。\n日期：{dates}\n酒店任务：{targets}，房型记录：{observations}\n本轮无涨跌达到 10 元的房型。\n完成时间：{now:%Y-%m-%d %H:%M}"
    else:
        # Individual price alerts were already sent by the collector.
        return

    if dry_run:
        print(f"Would notify: {title} | {body.replace(chr(10), ' / ')}")
        return
    send_alert(title, body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RateGuard Ctrip monitoring schedule")
    parser.add_argument("--mode", choices=("hourly", "anchors"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = datetime.now()

    if args.mode == "hourly":
        # 24:00 is the following day's 00:00 anchor run, so daytime polling is 08:00–23:00.
        if not 8 <= now.hour <= 23:
            print(f"Skip hourly collection outside 08:00–23:00: {now:%Y-%m-%d %H:%M}")
            return 0
        checkins = [now.date()]
    else:
        checkins = _anchor_dates(now.date())

    exit_code = 0
    summaries: list[dict[str, int | str]] = []
    with tempfile.TemporaryDirectory(prefix="rateguard-schedule-") as temp_dir:
        for checkin in checkins:
            summary_path = Path(temp_dir) / f"{checkin.isoformat()}.json"
            run_code, summary = _run(checkin, args.dry_run, summary_path)
            exit_code = max(exit_code, run_code)
            summaries.append(summary)
    _send_cycle_status(args.mode, now, summaries, exit_code, args.dry_run)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
