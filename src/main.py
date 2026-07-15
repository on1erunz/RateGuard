"""
main.py — RateGuard 主入口

v1.0-MVP 分两次数据路径：

  ① demo_mode（默认）
     使用本地生成器模拟附近酒店价格数据。
     适用：本地测试、架构演示、规则引擎验证。
     无需网络，秒级跑完。

  ② live_mode
     从真实 OTA 抓取价格（需要反爬就位后启用）。
     当前为占位，TODO stage2。

用法：
    python -m src.main [--city 深圳] [--max-hotels 10]  # demo 模式（默认）
    python -m src.main --live             # 真实目标抓取
    python -m src.main --city 深圳 --max 30

config.yaml 配置项：
    search:
      mode: city | coords（按坐标可选 Radius 搜索范围 km）
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.config import config
from src.database import log_price
from src.rules import PriceEvent, run_checks

# ── 第二阶段：真实 OTA 爬取（当前为隐身模式，通过 --live 打开）────
# try:
#     from src.scrapers.ctrip import CtripScraper
#     _CTRIP_AVL = True
# except ImportError:
#     _CTRIP_AVL = False

(_ROOT / "logs").mkdir(exist_ok=True)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_ROOT / "logs" / "rateguard.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("rateguard")


# ═══════════════════════════════════════════════════════════════════════════
# Demo 数据生成器（v1.0 占位）
# ═══════════════════════════════════════════════════════════════════════════

# 按城市市场基准价（2015价带 / 2027-05 估测）
_CITY_BASE: dict[str, dict[str, float]] = {
    "si": {"经济房": 250, "标准间": 370, "高级大床房": 480, "豪华套房": 880},
    "bj": {"经济房": 320, "标准间": 510, "高级大床房": 720, "豪华套房": 1350},
    "sh": {"经济房": 310, "标准间": 500, "高级大床房": 700, "豪华套房": 1250},
    "gz": {"经济房": 260, "标准间": 390, "高级大床房": 510, "豪华套房": 960},
    "hz": {"经济房": 270, "标准间": 410, "高级大床房": 540, "豪华套房": 1010},
}

_THEMES = [
    "希尔顿欢朋", "如家精选", "全季酒店", "亚朵酒店", "七天快捷",
    "汉庭优佳", "桔子水晶", "智选假日", "维也纳国际", "格林豪泰",
    "锦江之星", "尚客优品", "铂涛连锁", "速8精选", "精品壹号",
]
_ADJECTIVES = [
    "精品", "国际", "商务", "快捷", "精选", "希尔顿", "希尔顿",
]
_LOCALITY = [
    "中心广场", "北站", "会展中心", "科技园", "机场高速口",
    "南山商圈", "福田CBD", "罗湖口岸", "宝安中心", "前海湾",
]
_SUFFIX = [
    "酒店", "公寓", "民宿", "宾馆", "旅馆",
]


def _demo_hotels(city: str, count: int, seed: int) -> list[dict]:
    """种子一致生成器：同参数 → 同一组数据（方便测试对比）"""
    rng = random.Random(seed)
    city_key = city[:2].lower() if len(city) >= 2 else "si"
    base_prices = _CITY_BASE.get(city_key, _CITY_BASE["si"])
    room_types = list(base_prices.keys())
    hotels = []

    for i in range(count):
        adj = rng.choice(_ADJECTIVES)
        loc = rng.choice(_LOCALITY)
        brand = rng.choice(_THEMES)
        name = f"{brand if rng.random() < 0.6 else adj}{loc}{rng.choice(_SUFFIX)}"

        room_prices = {}
        for rt in room_types:
            bp = base_prices[rt]
            # 模拟±15% 波动
            import math
            v = bp * (1 + (rng.gauss(0, 0.12)))
            room_prices[rt] = max(99.0, round(v))

        hotels.append({"name": name, "rooms": room_prices})

    return hotels


def _list_checkin() -> str:
    return (datetime.now() + __import__("datetime").timedelta(days=14)).strftime("%Y-%m-%d")


def _list_checkout(checkin: str) -> str:
    from datetime import datetime, timedelta
    d = datetime.strptime(checkin, "%Y-%m-%d") + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RateGuard — 酒店 OTA 价格监控")
    p.add_argument("--city", type=str, default="", help="搜索城市，如 深圳")
    p.add_argument("--max-hotels", type=int, default=0, help="最多抓取酒店数")
    p.add_argument("--live", action="store_true", help="真实 OTA 抓取模式（默认 demo）")
    p.add_argument("--config", type=str, default="", help="指定 config.yaml 路径")
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

async def main_async() -> None:
    args = parse_args()
    city = args.city or config.get("search.city") or "深圳市"
    max_hotels = args.max_hotels or int(config.get("competitors.max_hotels", 15))
    live = args.live
    checkin = _list_checkin()
    checkout = _list_checkout(checkin)

    run_id = hashlib.md5(datetime.now().isoformat().encode()).hexdigest()[:12]
    seed = int(hashlib.md5(f"{run_id}:{city}".encode()).hexdigest()[:8], 16)
    logger.info(f"[rateguard] run_id={run_id}  city={city}  hotels={max_hotels}"
                f"  mode={'live' if live else 'demo'}  ")
    (_ROOT / "logs").mkdir(exist_ok=True)
    (_ROOT / "db").mkdir(exist_ok=True)

    # ═══════════════─ 数据采集 ─═══════════════════
    if live:
        # TODO: 第二阶段接入 Ctrip tBrowser across (crawling)
        logger.warning("[live] 真实爬取模式尚未完全就绪，回退到 demo 模式")
        listings = _demo_listings(city, max_hotels, seed)
    else:
        listings = _demo_listings(city, max_hotels, seed)

    if not listings:
        logger.error("[rateguard] 数据源为空 — 异常退出")
        return

    logger.info(f"[rateguard] 获取 {len(listings)} 条数据")

    # ═══════════════─ 写入 SQLite ─═══════════════════
    written = 0
    for h in listings:
        for room_type, price in h["rooms"].items():
            log_price(
                run_id=run_id,
                platform="demo" if not live else "ctrip",
                hotel_name=h["name"],
                room_type=room_type,
                checkin=checkin,
                checkout=checkout,
                price_yuan=price,
                hotel_id=hashlib.md5(h["name"].encode()).hexdigest()[:10],
            )
            written += 1
    logger.info(f"[rateguard] 写入 {written} 条 → db/prices ")

    # ═══════════════─ 规则引擎 ─═══════════════════
    events = [
        PriceEvent(
            platform="demo" if not live else "ctrip",
            hotel_name=h["name"],
            room_type=list(h["rooms"].keys())[0] if h["rooms"] else "未知房型",
            checkin=checkin,
            price_yuan=next(iter(h["rooms"].values()), None),
        )
        for h in listings
    ]
    alerts = run_checks(events, send_notify=False)
    if alerts:
        for a in alerts:
            logger.warning(f"  [{a.severity}] {a.hotel} → {a.message}")
    logger.info(f"[rateguard] 规则引擎完成，告警 {len(alerts)} 条")

    logger.info(f"[rateguard] run_id={run_id} 结束 ✅")


def _demo_listings(city: str, max_hotels: int, seed: int) -> list[dict]:
    return _demo_hotels(city, max_hotels, seed)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
