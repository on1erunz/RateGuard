"""固定携程酒店详情页的房型价格采集 MVP。

只使用正常浏览器访问和用户自行登录的会话，不重放或构造携程接口请求。

用法：
  # 首次执行：打开浏览器，手动完成携程登录后按 Enter 保存会话
  python -m src.ctrip_mvp --login

  # 采集默认的四家酒店（默认查询明天入住、一晚）
  python -m src.ctrip_mvp

  # 指定入住日期
  python -m src.ctrip_mvp --checkin 2026-07-20
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Browser, BrowserContext, Page, Response, async_playwright

from src.config import config
from src.database import log_price
from src.notify import send_alert

_ROOT = Path(__file__).resolve().parents[1]
_LOG = logging.getLogger("rateguard.ctrip_mvp")
_ROOM_LIST_PATH = "/getHotelRoomListInland"


@dataclass(frozen=True)
class Target:
    hotel_id: str
    name: str
    url: str


@dataclass
class Observation:
    hotel_id: str
    hotel_name: str
    room_id: str
    room_name: str
    rate_plan_key: str
    price_yuan: float | None
    is_available: bool
    status: str
    detail: str = ""


@dataclass
class CaptureResult:
    target: Target
    checkin: str
    checkout: str
    observations: list[Observation]
    raw_json_path: str
    html_path: str
    screenshot_path: str
    error: str = ""


def _settings() -> dict[str, Any]:
    return config.get("ctrip_mvp") or {}


def _targets() -> list[Target]:
    targets: list[Target] = []
    for item in _settings().get("targets", []):
        if not isinstance(item, dict):
            continue
        hotel_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if hotel_id and name and url:
            targets.append(Target(hotel_id, name, url))
    if not targets:
        raise RuntimeError("configs/config.yaml 中没有 ctrip_mvp.targets")
    return targets


def _resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (_ROOT / path).resolve()


def _dates(checkin_text: str) -> tuple[str, str]:
    if checkin_text:
        checkin = date.fromisoformat(checkin_text)
    else:
        checkin = date.today() + timedelta(days=1)
    return checkin.isoformat(), (checkin + timedelta(days=1)).isoformat()


def _base_url(url: str) -> str:
    """日期由页面控件设置，避免携程忽略直接拼接的 URL 参数。"""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("_") or "capture"


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "price", "displayPrice", "totalPrice", "averagePrice"):
            found = _money(value.get(key))
            if found is not None:
                return found
        return None
    match = re.search(r"(?:¥|￥)?\s*([0-9]+(?:\.[0-9]+)?)", str(value))
    return float(match.group(1)) if match else None


def _plan_price(plan: dict[str, Any]) -> float | None:
    price_info = plan.get("priceInfo") or {}
    for key in ("displayPrice", "price", "amount", "totalPrice", "averagePrice"):
        price = _money(price_info.get(key))
        if price is not None:
            return price
    for key in ("price", "displayPrice", "totalPrice", "averagePrice"):
        price = _money(plan.get(key))
        if price is not None:
            return price
    return None


def _rate_plans(node: Any) -> Iterable[dict[str, Any]]:
    """在携程响应中查找带物理房型 ID 的售卖计划。"""
    if isinstance(node, dict):
        if "physicalRoomId" in node and (
            "bookingStatusInfo" in node or "priceInfo" in node
        ):
            yield node
            return
        for value in node.values():
            yield from _rate_plans(value)
    elif isinstance(node, list):
        for value in node:
            yield from _rate_plans(value)


def _is_no_breakfast(meal: str) -> bool:
    """Recognise the labels currently used by Ctrip for no-breakfast plans."""
    normalised = meal.replace(" ", "").strip()
    return normalised in {"无早餐", "不含早餐", "无早"}


def _parse_rooms(payload: dict[str, Any], target: Target) -> list[Observation]:
    data = payload.get("data") or {}
    physical = data.get("physicRoomMap") or {}
    observations: list[Observation] = []
    seen: set[str] = set()

    for plan in _rate_plans(data):
        physical_id = str(plan.get("physicalRoomId", ""))
        physical_room = physical.get(physical_id) or physical.get(int(physical_id)) or {}
        room_name = str(physical_room.get("name") or plan.get("name") or "未知房型")
        plan_id = str(plan.get("id") or plan.get("roomKey") or plan.get("skey") or "")
        rate_plan_key = f"{physical_id}:{plan_id}"
        if rate_plan_key in seen:
            continue
        seen.add(rate_plan_key)

        booking = plan.get("bookingStatusInfo") or {}
        meal = str((plan.get("mealInfo") or {}).get("title") or "")
        if _settings().get("meal_filter", "no_breakfast") == "no_breakfast" and not _is_no_breakfast(meal):
            continue
        is_sold_out = bool(booking.get("isFullRoom")) or booking.get("isBooking") is False
        price_hidden = bool(booking.get("isHidePrice")) or booking.get("buttonText") == "登录看低价"
        price = _plan_price(plan)

        if is_sold_out:
            status = "sold_out"
        elif price_hidden:
            status = "price_hidden"
        elif price is None:
            status = "manual_review"
        else:
            status = "available"

        observations.append(Observation(
            hotel_id=target.hotel_id,
            hotel_name=target.name,
            room_id=physical_id,
            room_name=room_name,
            rate_plan_key=rate_plan_key,
            price_yuan=price,
            is_available=status == "available",
            status=status,
            detail=str(booking.get("buttonText") or ""),
        ))

    if not observations and data.get("isRoomListSoldOut"):
        observations.append(Observation(
            hotel_id=target.hotel_id,
            hotel_name=target.name,
            room_id="",
            room_name="全部房型",
            rate_plan_key="sold_out",
            price_yuan=None,
            is_available=False,
            status="sold_out",
        ))
    return observations


def _calendar_label(day: date) -> str:
    weekdays = "一二三四五六日"
    return f"{day.year}年{day.month}月{day.day}日(星期{weekdays[day.weekday()]})"


async def _await_room_payload(
    page: Page, url: str, checkin: str, checkout: str, timeout_s: int
) -> tuple[dict[str, Any] | None, str]:
    """通过详情页日期控件选择入住/离店日，并读取该操作触发的房型响应。"""
    try:
        await page.goto(_base_url(url), wait_until="domcontentloaded", timeout=timeout_s * 1000)
        # 详情页的日期选择器由客户端在首轮房型数据返回后才完成绑定。
        await page.wait_for_timeout(4000)
        date_boxes = page.get_by_role("textbox", name="选择日期")
        # 前两个是页头搜索区，第三个是房型列表自身的日期控件。
        await date_boxes.nth(2).click(timeout=timeout_s * 1000)
        await page.wait_for_timeout(800)
        checkin_day = date.fromisoformat(checkin)
        checkout_day = date.fromisoformat(checkout)
        await page.get_by_role("checkbox", name=re.compile(rf"^{re.escape(_calendar_label(checkin_day))}")).click(
            timeout=timeout_s * 1000
        )
        async with page.expect_response(
            lambda response: _ROOM_LIST_PATH in response.url,
            timeout=timeout_s * 1000,
        ) as response_info:
            await page.get_by_role("checkbox", name=re.compile(rf"^{re.escape(_calendar_label(checkout_day))}")).click(
                timeout=timeout_s * 1000
            )
        response = await response_info.value
        return await response.json(), ""
    except Exception as exc:
        return None, str(exc)


async def _capture_target(
    context: BrowserContext,
    target: Target,
    checkin: str,
    checkout: str,
    raw_dir: Path,
    timeout_s: int,
    run_id: str,
) -> CaptureResult:
    raw_dir.mkdir(parents=True, exist_ok=True)
    page = await context.new_page()
    stem = f"{run_id}_{_safe_name(target.hotel_id)}"
    json_path = raw_dir / f"{stem}.json"
    html_path = raw_dir / f"{stem}.html"
    screenshot_path = raw_dir / f"{stem}.png"
    try:
        payload, error = await _await_room_payload(page, target.url, checkin, checkout, timeout_s)
        evidence_errors: list[str] = []
        try:
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            evidence_errors.append(f"screenshot unavailable: {exc}")
        try:
            html_path.write_text(await page.content(), encoding="utf-8")
        except Exception as exc:
            evidence_errors.append(f"html unavailable: {exc}")
        if evidence_errors:
            error = "; ".join(part for part in [error, *evidence_errors] if part)
        if payload is None:
            return CaptureResult(target, checkin, checkout, [], "", str(html_path), str(screenshot_path), error)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        actual_checkin = str((payload.get("data") or {}).get("searchBoxInfo", {}).get("checkIn", ""))
        expected_checkin = checkin.replace("-", "")
        observations = _parse_rooms(payload, target)
        if actual_checkin and actual_checkin != expected_checkin:
            error = f"日期不匹配：请求 {expected_checkin}，页面实际返回 {actual_checkin}"
            for obs in observations:
                obs.status = "manual_review"
                obs.detail = error
        return CaptureResult(
            target, checkin, checkout, observations,
            str(json_path), str(html_path), str(screenshot_path), error,
        )
    finally:
        if not page.is_closed():
            await page.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ctrip_mvp_runs (
  run_id TEXT PRIMARY KEY,
  checkin TEXT NOT NULL,
  checkout TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  target_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ctrip_mvp_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'ctrip',
  hotel_id TEXT NOT NULL,
  hotel_name TEXT NOT NULL,
  room_id TEXT NOT NULL,
  room_name TEXT NOT NULL,
  rate_plan_key TEXT NOT NULL,
  meal TEXT,
  cancel_policy TEXT,
  checkin TEXT NOT NULL,
  checkout TEXT NOT NULL,
  price_yuan REAL,
  previous_price_yuan REAL,
  price_delta_yuan REAL,
  is_available INTEGER NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  raw_json_path TEXT,
  html_path TEXT,
  screenshot_path TEXT,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ctrip_mvp_lookup
ON ctrip_mvp_observations (hotel_id, room_id, rate_plan_key, checkin, fetched_at);
"""


def _db_path() -> Path:
    return _resolve(str(config.get("scraper.persist_path") or "./db/rateguard.db"))


def _save_results(run_id: str, results: list[CaptureResult], threshold: float) -> tuple[int, int]:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    alerts = 0
    dashboard_rows: list[tuple[CaptureResult, Observation]] = []
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO ctrip_mvp_runs (run_id,checkin,checkout,started_at,target_count,error_count) VALUES (?,?,?,?,?,?)",
            (run_id, results[0].checkin, results[0].checkout, now, len(results), sum(bool(r.error) for r in results)),
        )
        for result in results:
            for obs in result.observations:
                previous = None
                if obs.price_yuan is not None:
                    row = conn.execute(
                        """SELECT price_yuan FROM ctrip_mvp_observations
                           WHERE hotel_id=? AND room_id=? AND rate_plan_key=? AND checkin=?
                             AND price_yuan IS NOT NULL
                           ORDER BY id DESC LIMIT 1""",
                        (obs.hotel_id, obs.room_id, obs.rate_plan_key, result.checkin),
                    ).fetchone()
                    previous = float(row[0]) if row else None
                delta = round(obs.price_yuan - previous, 2) if previous is not None and obs.price_yuan is not None else None
                conn.execute(
                    """INSERT INTO ctrip_mvp_observations (
                       run_id,hotel_id,hotel_name,room_id,room_name,rate_plan_key,
                       checkin,checkout,price_yuan,previous_price_yuan,price_delta_yuan,is_available,status,
                       detail,raw_json_path,html_path,screenshot_path,fetched_at
                     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, obs.hotel_id, obs.hotel_name, obs.room_id, obs.room_name, obs.rate_plan_key,
                     result.checkin, result.checkout, obs.price_yuan, previous,
                     delta, int(obs.is_available), obs.status, obs.detail, result.raw_json_path,
                     result.html_path, result.screenshot_path, now),
                )
                saved += 1
                if obs.price_yuan is not None:
                    dashboard_rows.append((result, obs))
                if delta is not None and abs(delta) >= threshold:
                    alerts += 1
                    direction = "上涨" if delta > 0 else "下降"
                    send_alert(
                        f"[携程价格{direction}] {obs.hotel_name}",
                        f"{result.checkin}｜{obs.room_name}\n当前 ¥{obs.price_yuan:.0f}，上一轮 ¥{previous:.0f}，变动 {delta:+.0f} 元",
                    )
        conn.execute("UPDATE ctrip_mvp_runs SET finished_at=? WHERE run_id=?", (datetime.now().isoformat(timespec="seconds"), run_id))

    # dataset 会单独打开 SQLAlchemy 连接；必须在上面的 SQLite 事务提交后再更新旧看板表。
    for result, obs in dashboard_rows:
        log_price(
            run_id=run_id, platform="ctrip", hotel_id=obs.hotel_id,
            hotel_name=obs.hotel_name, room_type=obs.room_name,
            checkin=result.checkin, checkout=result.checkout, price_yuan=obs.price_yuan,
            includes_breakfast=False, source_url=result.target.url,
            raw_html_hash=hashlib.sha256(result.raw_json_path.encode()).hexdigest()[:12],
        )
    try:
        from src.dashboard_export import write_snapshot
        write_snapshot()
    except Exception as exc:
        _LOG.warning("Unable to update dashboard snapshot: %s", exc)
    return saved, alerts


async def _run_collection(checkin: str, headed: bool, run_id: str, hotel_ids: set[str]) -> list[CaptureResult]:
    settings = _settings()
    state_path = _resolve(str(settings.get("storage_state") or "./.secrets/ctrip_state.json"))
    raw_dir = _resolve(str(settings.get("raw_capture_dir") or "./output/playwright/ctrip"))
    timeout_s = int(settings.get("timeout_s", 45))
    targets = [target for target in _targets() if not hotel_ids or target.hotel_id in hotel_ids]
    if not targets:
        raise RuntimeError("指定的 --hotel-id 不在 ctrip_mvp.targets 中")
    checkout = (date.fromisoformat(checkin) + timedelta(days=1)).isoformat()
    if not state_path.exists():
        _LOG.warning("未找到登录会话：将执行未登录验证，价格可能显示为‘登录看低价’。")

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=not headed)
        context = await browser.new_context(storage_state=str(state_path) if state_path.exists() else None, locale="zh-CN")
        try:
            results = []
            for target in targets:
                _LOG.info("采集 %s", target.name)
                results.append(await _capture_target(context, target, checkin, checkout, raw_dir, timeout_s, run_id))
            return results
        finally:
            await context.close()
            await browser.close()


async def _interactive_login() -> None:
    settings = _settings()
    state_path = _resolve(str(settings.get("storage_state") or "./.secrets/ctrip_state.json"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(locale="zh-CN")
        try:
            page = await context.new_page()
            await page.goto(_targets()[0].url, wait_until="domcontentloaded")
            print("请在已打开的浏览器中按正常流程登录携程，确认完成后回到此终端按 Enter 保存会话。")
            await asyncio.to_thread(input)
            await context.storage_state(path=str(state_path), indexed_db=True)
            print(f"会话已保存到：{state_path}")
        finally:
            await context.close()
            await browser.close()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RateGuard Ctrip 固定酒店 MVP")
    parser.add_argument("--checkin", default="", help="入住日期 YYYY-MM-DD，默认明天")
    parser.add_argument("--headed", action="store_true", help="采集时显示浏览器窗口")
    parser.add_argument("--login", action="store_true", help="手动登录并保存本机会话")
    parser.add_argument("--hotel-id", action="append", default=[], help="仅采集指定酒店 ID，可重复使用")
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=float(_settings().get("alert_threshold_yuan", 10)),
        help="价格变化通知阈值（元）",
    )
    parser.add_argument(
        "--summary-file",
        default="",
        help="写入本次采集汇总 JSON 的文件路径，供定时任务发送周期状态通知",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = _args()
    if args.login:
        asyncio.run(_interactive_login())
        return
    checkin, _ = _dates(args.checkin)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    results = asyncio.run(_run_collection(checkin, args.headed, run_id, set(args.hotel_id)))
    saved, alerts = _save_results(run_id, results, args.alert_threshold)
    if args.summary_file:
        summary_path = Path(args.summary_file)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "checkin": checkin,
                    "target_count": len(results),
                    "observation_count": saved,
                    "price_alert_count": alerts,
                    "error_count": sum(bool(result.error) for result in results),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    for result in results:
        if result.error:
            print(f"{result.target.name}: 失败 — {result.error}")
            continue
        statuses: dict[str, int] = {}
        for obs in result.observations:
            statuses[obs.status] = statuses.get(obs.status, 0) + 1
        print(f"{result.target.name}: {len(result.observations)} 个房型/售卖计划，{statuses}")
    print(f"已写入 {saved} 条观察记录，触发 {alerts} 条价格变动通知。")


if __name__ == "__main__":
    main()
