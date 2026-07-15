"""Create the sanitized dashboard snapshot consumed by the Vercel site."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import config

ROOT = Path(__file__).resolve().parents[1]


def _db_path() -> Path:
    configured = Path(str(config.get("scraper.persist_path") or "./db/rateguard.db"))
    return configured if configured.is_absolute() else (ROOT / configured).resolve()


def _history_days() -> int:
    return max(1, int(config.get("dashboard.history_days", 30)))


def _own_hotel_ids() -> set[str]:
    targets = config.get("ctrip_mvp.targets") or []
    return {
        str(item.get("id"))
        for item in targets
        if isinstance(item, dict) and item.get("role") == "own" and item.get("id")
    }


def _active_checkin_from(now: datetime) -> str:
    """Keep the previous check-in date visible until the following day's noon."""
    clear_hour = min(23, max(0, int(config.get("dashboard.previous_day_clear_hour", 12))))
    active_date = now.date() if now.hour >= clear_hour else now.date() - timedelta(days=1)
    return active_date.isoformat()


def _load_rows(since: str) -> list[dict[str, Any]]:
    """Load the display-safe observation fields from the local history database."""
    db_path = _db_path()
    rows: list[dict[str, Any]] = []

    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            result = connection.execute(
                """
                SELECT platform, hotel_id, hotel_name, room_id, room_name,
                       rate_plan_key, checkin, checkout, price_yuan,
                       previous_price_yuan, price_delta_yuan, is_available,
                       status, fetched_at
                FROM ctrip_mvp_observations
                WHERE fetched_at >= ?
                ORDER BY fetched_at DESC
                """,
                (since,),
            )
            rows = [dict(row) for row in result]
    return rows


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the most recent observation for every hotel/room/rate-plan/date."""
    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row["platform"]), str(row["hotel_id"]), str(row["room_id"]),
            str(row["rate_plan_key"]), str(row["checkin"]),
        )
        if key not in latest or str(row["fetched_at"]) > str(latest[key]["fetched_at"]):
            latest[key] = row
    return list(latest.values())


def _write_future_price_sheet(rows: list[dict[str, Any]], generated_at: datetime, own_hotel_ids: set[str]) -> Path:
    """Create a compact, normalized JSON sheet for future-date price planning."""
    destination = ROOT / "output" / "dashboard" / "future-room-prices.json"
    tomorrow = (generated_at.date() + timedelta(days=1)).isoformat()
    grouped: dict[str, dict[str, Any]] = {}

    for row in _latest_rows(rows):
        if str(row["checkin"]) < tomorrow:
            continue
        checkin = str(row["checkin"])
        date_entry = grouped.setdefault(checkin, {"checkin": checkin, "checkout": row["checkout"], "hotels": {}})
        hotels: dict[str, Any] = date_entry["hotels"]
        hotel = hotels.setdefault(str(row["hotel_id"]), {
            "hotel_id": row["hotel_id"], "hotel_name": row["hotel_name"],
            "is_own_hotel": row["hotel_id"] in own_hotel_ids,
            "updated_at": row["fetched_at"], "rooms": [],
        })
        if str(row["fetched_at"]) > str(hotel["updated_at"]):
            hotel["updated_at"] = row["fetched_at"]
        hotel["rooms"].append({
            "room_id": row["room_id"], "room_name": row["room_name"],
            "rate_plan_key": row["rate_plan_key"], "price_yuan": row["price_yuan"],
            "status": row["status"], "is_available": bool(row["is_available"]),
            "updated_at": row["fetched_at"],
        })

    dates: list[dict[str, Any]] = []
    for checkin in sorted(grouped):
        entry = grouped[checkin]
        hotels = list(entry.pop("hotels").values())
        for hotel in hotels:
            hotel["rooms"].sort(key=lambda room: (
                room["price_yuan"] is None,
                room["price_yuan"] if room["price_yuan"] is not None else float("inf"),
                str(room["room_name"]),
            ))
        entry["hotels"] = sorted(hotels, key=lambda hotel: str(hotel["hotel_name"]))
        dates.append(entry)

    payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "platform": "ctrip",
        "scope": "future check-in dates only; no-breakfast room plans",
        "currency": "CNY",
        "unit": "price_yuan_per_night",
        "dates": dates,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def write_snapshot() -> Path:
    """Export dashboard data and the normalized future-room-price download sheet."""
    destination = ROOT / "output" / "dashboard" / "dashboard.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now()
    since = (generated_at - timedelta(days=_history_days())).isoformat(timespec="seconds")
    rows = _load_rows(since)

    own_hotel_ids = _own_hotel_ids()
    for row in rows:
        row["is_own_hotel"] = row["hotel_id"] in own_hotel_ids

    payload = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "history_days": _history_days(),
        "active_checkin_from": _active_checkin_from(generated_at),
        "observations": rows,
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _write_future_price_sheet(rows, generated_at, own_hotel_ids)
    return destination


if __name__ == "__main__":
    print(write_snapshot())
