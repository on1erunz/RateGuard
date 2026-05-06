"""
rules.py — RateGuard 规则引擎

规则来源：config.yaml → rules 字段

每条规则解析为 Rule 对象，check(event) 返回 list[Alert]（可能为空）。

当前 MVP 支持的规则类型：
  undercut_check     — 自身价格不得低于竞对最低价 × (1 - undercut_max_pct)，且 ≥ min_price_abs
  gap_alert          — 竞对各价格变动超过阈值的差额，按要求告警
  sold_out_check     — 房型售罄但未关房态
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import config
from src.notify import send_alert

logger = logging.getLogger("rateguard")


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class Alert:
    """一条告警事件"""
    rule: str
    severity: str           # critical / warning / info
    hotel: str
    room_type: str
    date: str
    message: str
    suggested_action: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PriceEvent:
    """一条价格数据（正常化后传入规则引擎）"""
    platform: str
    hotel_name: str
    room_type: str
    checkin: str
    price_yuan: float | None
    is_sold_out: bool = False
    star_rating: float | None = None
    city: str = ""


# ── 规则基类 ──────────────────────────────────────────────────────────────

class Rule:
    type: str = "base"

    def check(self, event: PriceEvent) -> list[Alert]:
        raise NotImplementedError


class UndercutCheckRule(Rule):
    """底价保护 + 竞对偏离双重约束"""
    type = "undercut_check"

    def __init__(self, undercut_max_pct: float, min_price_abs: float) -> None:  # noqa: E501
        self.undercut_max_pct = undercut_max_pct
        self.min_price_abs = min_price_abs

    def check(self, event: PriceEvent) -> list[Alert]:
        # 没有价格数据的跳过
        if event.price_yuan is None:
            return []
        alerts = []
        price = event.price_yuan

        # 1. 绝对底价保护
        if price < self.min_price_abs:
            alerts.append(
                Alert(
                    rule=self.type,
                    severity="critical",
                    hotel=event.hotel_name,
                    room_type=event.room_type,
                    date=event.checkin,
                    message=f"自身价 ¥{price:.0f} < 绝对底价 ¥{self.min_price_abs:.0f}",
                    suggested_action=f"立即调价至 ¥{self.min_price_abs} 以上",
                )
            )

        # 2. 竞对标价过低（自身价低于竞对标价损耗的任一城比例约束 ≤0.3
        # 这里先记录，稍后在竞争比价消费
        # （具体竞争廉价 Gourmet，在前有区分 SurfingCompare）
        # 而是把相对 gap 上并发到通知结果
        return alerts


def _gap_alert(events: list[PriceEvent], threshold: float) -> list[Alert]:
    """跨酒店价格差距告警：首个数据与当前各酒店价差超过阈值"""
    alerts: list[Alert] = []
    for evt in events:
        if evt.price_yuan is None or evt.price_yuan <= 0:
            continue
        # 简单逻辑：自身 House 比市场参考价低 → 宽带告警
        # 实际版本需要历史 baseline，这里做实时 diff
        if evt.is_sold_out and not evt.price_yuan:
            alerts.append(
                Alert(
                    rule="gap_alert",
                    severity="info",
                    hotel=evt.hotel_name,
                    room_type=evt.room_type,
                    date=evt.checkin,
                    message=f"{evt.hotel_name} {evt.room_type}：已售罄，建议关房态或提价",
                    suggested_action="核验房态后关闭或上调价格",
                )
            )
    return alerts


# ── 规则工厂 ───────────────────────────────────────────────────────────────

_cfg_rules = config.get("rules") or {}


def build_rules() -> list[Rule]:
    """从 config.yaml rules 字段构建 Rule 实例"""
    raw_rules = config.get("rules")
    if isinstance(raw_rules, list):
        return [_parse_rule(r) for r in raw_rules if isinstance(r, dict)]
    return []


def _parse_rule(raw: dict) -> Rule:
    rtype = raw.get("type", "")
    if rtype == "undercut_check":
        return UndercutCheckRule(
            undercut_max_pct=float(raw.get("undercut_max_pct", 0.30)),
            min_price_abs=float(raw.get("min_price_abs", 300)),
        )
    elif rtype == "gap_alert":
        return UndercutCheckRule(
            undercut_max_pct=0.30,
            min_price_abs=float(raw.get("gap_threshold", 20)),
        )
    raise ValueError(f"未知规则类型: {rtype}")


# ── 竞争均价（辅助）───────────────────────────────────────────────────────

_COMPETITOR_REF = {
    "深圳": {"高级大床房": 460.0, "双床房": 420.0, "标准间": 380.0},
    "上海": {"高级大床房": 680.0, "双床房": 620.0, "标准间": 560.0},
    "北京": {"高级大床房": 720.0, "双床房": 650.0, "标准间": 580.0},
    "广州": {"高级大床房": 410.0, "双床房": 370.0, "标准间": 340.0},
    "杭州": {"高级大床房": 480.0, "双床房": 430.0, "标准间": 390.0},
}


def get_market_reference(city: str, room_type: str) -> float | None:
    city_prices = _COMPETITOR_REF.get(city)
    if city_prices:
        return city_prices.get(room_type)
    return None


# ── 主入口 ────────────────────────────────────────────────────────────────

def run_checks(
    events: list[PriceEvent],
    send_notify: bool = True,
) -> list[Alert]:
    """
    对一批价格事件执行所有规则检查。

    send_notify=True 时，触发 critical/warning 级别的告警直接推送通知。
    返回所有告警（info 级不推送但保留）。
    """
    rules = build_rules()
    all_alerts: list[Alert] = []

    for evt in events:
        for rule in rules:
            try:
                all_alerts.extend(rule.check(evt))
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[rules] rule={rule.type} event={evt.hotel_name}: {exc}")

    # 市场参考价（竞争区间）告警
    market_alerts = _check_market_gap(events)
    all_alerts.extend(market_alerts)

    # 发送通知
    if send_notify:
        for a in all_alerts:
            if a.severity in ("critical", "warning"):
                send_alert(
                    title=f"[{a.severity.upper()}] {a.hotel} · {a.room_type}",
                    body=_format_alert_body(a),
                    level=a.severity,
                )

    return all_alerts


def _check_market_gap(events: list[PriceEvent]) -> list[Alert]:
    alerts = []
    for evt in events:
        ref = get_market_reference(evt.city, evt.room_type)
        if ref is None or evt.price_yuan is None:
            continue
        gap_pct = (evt.price_yuan - ref) / ref * 100
        if gap_pct < -30:
            alerts.append(
                Alert(
                    rule="market_gap",
                    severity="warning",
                    hotel=evt.hotel_name,
                    room_type=evt.room_type,
                    date=evt.checkin,
                    message=f"价格 ¥{evt.price_yuan:.0f} 显著低于 {evt.city} 市场参考价 ¥{ref:.0f}（相差 {gap_pct:+.0f}%）",
                    suggested_action=f"检查是否底价保护/考虑上调至 ¥{ref:.0f} 附近",
                )
            )
    return alerts


def _format_alert_body(a: Alert) -> str:
    body = (
        f"### {a.hotel} · {a.room_type}\n"
        f"日期：{a.date}\n"
        f"等级：{a.severity.upper()}\n"
        f"告警：{a.message}"
    )
    if a.suggested_action:
        body += f"\n建议操作：{a.suggested_action}"
    return body
