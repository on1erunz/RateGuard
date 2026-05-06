"""
database.py — RateGuard 本地数据库（SQLite）

表结构：
  price_log（价格流水） — 每次爬取写入一行
  hotels               — 酒店基础信息
  room_types           — 房型基准
  crawl_runs            — 每次运行记录（用于 dedup / retry 追踪）
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Iterator

import dataset

from src.config import config

_DB_PATH = config.get("scraper.persist_path") or "./db/rateguard.db"
_DB_PATH = str(Path(_DB_PATH).resolve())


def get_db() -> dataset.Database:
    """返回 dataset.Database 实例（惰性打开）。"""
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return dataset.connect(f"sqlite:///{_DB_PATH}")


# ── CREATE TABLE DDL ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_log (
    id              INTEGER       PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT          NOT NULL,
    platform        TEXT          NOT NULL,      -- 'ctrip' | 'meituan' | 'fliggy'
    hotel_id        TEXT                      ,
    hotel_name      TEXT                      ,
    room_type       TEXT                      ,
    price_yuan      REAL                      ,  -- float 支持价格含分（OTA 常见 ¥299.5）
    currency        TEXT   DEFAULT 'CNY',
    checkin         TEXT   NOT NULL,           -- ISO date 'YYYY-MM-DD'
    checkout        TEXT   NOT NULL,
    is_sold_out     BOOLEAN DEFAULT 0,
    includes_breakfast BOOLEAN DEFAULT 0,
    source_url      TEXT,
    raw_html_hash   TEXT,                      -- snippet hash 用于快速 diff
    fetched_at      TEXT   NOT NULL,           -- ISO 8601
    UNIQUE(run_id, platform, hotel_name, room_type, checkin)
);

CREATE TABLE IF NOT EXISTS hotels (
    id       TEXT PRIMARY KEY,   -- 酒店名称（稳定主键）
    platform TEXT,
    city     TEXT,
    address  TEXT,
    star_rating REAL,
    first_seen TEXT,
    last_seen  TEXT
);

CREATE TABLE IF NOT EXISTS room_types (
    id          TEXT PRIMARY KEY,  -- room_name
    hotel_id    TEXT,
    base_price  REAL,
    min_price   REAL,
    notes       TEXT,
    FOREIGN KEY (hotel_id) REFERENCES hotels(id)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id         TEXT     PRIMARY KEY,
    started_at     TEXT     NOT NULL,
    finished_at    TEXT,
    city           TEXT     NOT NULL,
    platform       TEXT     NOT NULL,
    total_hotels    INTEGER DEFAULT 0,
    total_records  INTEGER DEFAULT 0,
    success        BOOLEAN DEFAULT 0,   -- 1=全部完成, 0=部分失败, NULL=未知
    error_message  TEXT
);
"""  # noqa: E501


@contextlib.contextmanager
def open_conn() -> dataset.Database:
    """上下文形式的数据库连接，with 块退出时自动释放。"""
    db = get_db()
    try:
        yield db
    finally:
        with contextlib.suppress(Exception):
            db._engine.dispose()


# ── price_log 写入 ───────────────────────────────────────────────────────

def log_price(
    *,
    run_id: str,
    platform: str,
    hotel_name: str,
    room_type: str,
    checkin: str,
    checkout: str,
    price_yuan: float | None = None,
    is_sold_out: bool = False,
    includes_breakfast: bool = False,
    source_url: str = "",
    raw_html_hash: str = "",
    hotel_id: str = "",
    currency: str = "CNY",
) -> int:
    """写入一条价格记录（UPSERT：同 run_id + platform + hotel_name + room_type + checkin 覆盖）。"""
    db = get_db()
    table = db["price_log"]

    rec = {
        "run_id": run_id,
        "platform": platform,
        "hotel_id": hotel_id,
        "hotel_name": hotel_name,
        "room_type": room_type,
        "price_yuan": price_yuan,
        "currency": currency,
        "checkin": checkin,
        "checkout": checkout,
        "is_sold_out": int(is_sold_out),
        "includes_breakfast": int(includes_breakfast),
        "source_url": source_url,
        "raw_html_hash": raw_html_hash,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    # 先查是否存在（SQLite 无原生 UPSERT， dataset upsert 版本依赖 SQLAlchemy 2.x）
    row = table.find_one(
        run_id=run_id,
        platform=platform,
        hotel_name=hotel_name,
        room_type=room_type,
        checkin=checkin,
    )
    if row:
        table.update(rec, ["id"])
        return row["id"]
    else:
        return table.insert(rec)


# ── crawl_runs 记录 ──────────────────────────────────────────────────────

def start_run(run_id: str, city: str, platform: str) -> dict:
    db = get_db()
    db.begin()
    try:
        db._execute(
            _SCHEMA.lstrip() and db  # schema already created below
        )
    except Exception:
        pass

    crop = {
        "run_id": run_id,
        "started_at": datetime.utcnow().isoformat(),
        "city": city,
        "platform": platform,
        "success": False,
    }
    db["crawl_runs"].insert(crop)  # type: ignore[attr-defined]
    return crop


def finish_run(run_id: str, total_hotels: int, total_records: int, success: bool = True, error: str = "") -> None:
    db = get_db()
    db["crawl_runs"].update(
        dict(
            run_id=run_id,
            finished_at=datetime.utcnow().isoformat(),
            total_hotels=total_hotels,
            total_records=total_records,
            success=success,
            error_message=error,
        ),
        ["run_id"],
    )


# ── 查询辅助 ─────────────────────────────────────────────────────────────

def find_prices(
    platform: str | None = None,
    city: str | None = None,
    checkin: str | None = None,
    hotel_name: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """灵活条件查询价格流水，条件全空则返回最新 N 条。"""
    db = get_db()
    rows = db["price_log"].all()

    results = []
    for row in rows:
        if platform and row.get("platform") != platform:
            continue
        if city:
            # city → fetch from hotels table; skip records without hotel_id
            hid = row.get("hotel_id")
            if hid:
                h = db["hotels"].find_one(id=hid)
                if not (h and h.get("city") == city):
                    continue
        if checkin and row.get("checkin") != checkin:
            continue
        if hotel_name and row.get("hotel_name") != hotel_name:
            continue
        results.append(row)

    return results[offset : offset + limit]


# ── 初始化（自动建表） ───────────────────────────────────────────────────

def ensure_schema() -> None:
    db = get_db()
    with contextlib.suppress(Exception):
        db._engine.execute(_SCHEMA)


def get_db():  # re-export used above
    db = dataset.connect(f"sqlite:///{_DB_PATH}")
    try:
        db._engine.execute(_SCHEMA)
    except Exception:
        pass
    return db
