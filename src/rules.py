"""
rules.py - RateGuard rule engine

Rules: undercut_check, market_gap, sold_out_check
Config: config.yaml -> rules field
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import config
from src.notify import send_alert

logger = logging.getLogger("rateguard")


@dataclass
class Alert:
    rule: str
    severity: str
    hotel: str
    room_type: str
    date: str
    message: str
    suggested_action: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class PriceEvent:
    platform: str
    hotel_name: str
    room_type: str
    checkin: str
    price_yuan: float | None = None
    is_sold_out: bool = False
    star_rating: float | None = None
    city: str = ""


class Rule:
    type: str = "base"
    def check(self, event: PriceEvent) -> list[Alert]:
        raise NotImplementedError


class UndercutCheckRule(Rule):
    type = "undercut_check"
    def __init__(self, undercut_max_pct: float = 0.30, min_price_abs: float = 300.0):
        self.undercut_max_pct = undercut_max_pct
        self.min_price_abs = min_price_abs
    def check(self, event: PriceEvent) -> list[Alert]:
        if event.price_yuan is None:
            return []
        price = event.price_yuan
        if price < self.min_price_abs:
            return [Alert(
                rule=self.type, severity="critical",
                hotel=event.hotel_name, room_type=event.room_type,
                date=event.checkin,
                message=f"price yen{price:.0f} < floor yen{self.min_price_abs:.0f}",
                suggested_action=f"raise to >= yen{self.min_price_abs}",
            )]
        return []


class MarketGapRule(Rule):
    type = "market_gap"
    def __init__(self, gap_threshold_pct: float = 30.0):
        self.gap_threshold_pct = gap_threshold_pct
    def check(self, event: PriceEvent) -> list[Alert]:
        ref = _market_ref(event.city, event.room_type)
        if ref is None or event.price_yuan is None:
            return []
        gap_pct = (event.price_yuan - ref) / ref * 100
        if abs(gap_pct) < self.gap_threshold_pct:
            return []
        direction = "above" if gap_pct > 0 else "below"
        severity = "warning" if abs(gap_pct) <= 50 else "critical"
        return [Alert(
            rule=self.type, severity=severity,
            hotel=event.hotel_name, room_type=event.room_type,
            date=event.checkin,
            message=f"yen{event.price_yuan:.0f} {direction} ref yen{ref:.0f} ({gap_pct:+.0f}%)",
            suggested_action=f"check strategy, target range yen{ref*.8:.0f} ~ yen{ref*1.2:.0f}",
        )]


class SoldOutRule(Rule):
    type = "sold_out_check"
    def check(self, event: PriceEvent) -> list[Alert]:
        if event.is_sold_out:
            return [Alert(
                rule=self.type, severity="info",
                hotel=event.hotel_name, room_type=event.room_type,
                date=event.checkin,
                message=f"{event.hotel_name} {event.room_type} sold out",
                suggested_action="close listing or keep sold out",
            )]
        return []


_MARKET_REF = {
    "深圳": {"economic": 250, "standard": 370, "deluxe": 460, "suite": 880},
    "北京市": {"economic": 320, "standard": 510, "deluxe": 720, "suite": 1350},
    "上海": {"economic": 310, "standard": 500, "deluxe": 680, "suite": 1250},
    "广州": {"economic": 260, "standard": 390, "deluxe": 410, "suite": 960},
    "杭州": {"economic": 270, "standard": 410, "deluxe": 480, "suite": 1010},
    "成都": {"economic": 200, "standard": 310, "deluxe": 400, "suite": 760},
}


def _market_ref(city: str, room_type: str) -> float | None:
    if not city:
        return None
    m = _MARKET_REF.get(city) or _MARKET_REF.get(city[:2], {})
    return m.get(room_type)


def build_rules() -> list[Rule]:
    raw = config.get("rules")
    if not raw:
        return [UndercutCheckRule()]
    rules = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                t = r.get("type", "")
                if t == "undercut_check":
                    rules.append(UndercutCheckRule(
                        undercut_max_pct=float(r.get("undercut_max_pct", 0.30)),
                        min_price_abs=float(r.get("min_price_abs", 300)),
                    ))
                elif t == "market_gap":
                    rules.append(MarketGapRule(
                        gap_threshold_pct=float(r.get("gap_threshold_pct", 30)),
                    ))
    elif isinstance(raw, dict):
        if "undercut_max_pct" in raw or "min_price_abs" in raw:
            rules.append(UndercutCheckRule(
                undercut_max_pct=float(raw.get("undercut_max_pct", 0.30)),
                min_price_abs=float(raw.get("min_price_abs", 300)),
            ))
        if "gap_threshold_pct" in raw or "gap_alert_threshold" in raw:
            rules.append(MarketGapRule(
                gap_threshold_pct=float(raw.get("gap_threshold_pct") or raw.get("gap_alert_threshold", 20)),
            ))
        if raw.get("sold_out_check"):
            rules.append(SoldOutRule())
    return rules or [UndercutCheckRule()]


def run_checks(
    events: list[PriceEvent],
    send_notify: bool = True,
) -> list[Alert]:
    rules = build_rules()
    all_alerts = []
    for evt in events:
        for rule in rules:
            try:
                all_alerts.extend(rule.check(evt))
            except Exception as exc:
                logger.error(f"[rules] {rule.type} {evt.hotel_name}: {exc}")
    if send_notify:
        for a in all_alerts:
            if a.severity in ("critical", "warning"):
                send_alert(title=f"[{a.severity.upper()}] {a.hotel}.{a.room_type}", body=_fmt(a), level=a.severity)
    return all_alerts


def _fmt(a: Alert) -> str:
    b = f"### {a.hotel} - {a.room_type}\n{a.date}\n{a.severity.upper()}: {a.message}"
    if a.suggested_action:
        b += f"\nAction: {a.suggested_action}"
    return b


def get_market_reference(city: str, room_type: str) -> float | None:
    m = _MARKET_REF.get(city) or _MARKET_REF.get(city[:2], {})
    return m.get(room_type)
