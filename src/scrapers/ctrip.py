"""
scrapers/ctrip.py — 携程酒店价格抓取

搜索方式：
  1. 按城市名搜索（city 模式）→ 取酒店列表页
  2. 按坐标搜索（coords 模式）→ 取附近酒店列表

返回 list[HotelListing]，每项含：名称 / ID / 最低价 / 星级。
后续阶段再遍历酒店详情页逐房型抓取。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import random
import re
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import async_playwright, Page

logger = logging.getLogger("rateguard")


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class RoomInfo:
    name: str = ""
    price_yuan: float | None = None
    is_sold_out: bool = False
    includes_breakfast: bool = False


@dataclass
class HotelListing:
    name: str = ""
    hotel_id: str = ""
    star_rating: float | None = None
    address: str = ""
    min_price_yuan: float | None = None
    rooms: list[RoomInfo] = field(default_factory=list)
    source_url: str = ""
    lat: float | None = None
    lng: float | None = None
    city: str = ""
    _raw_html_hash: str = ""


# ── 辅助 ──────────────────────────────────────────────────────────────────

def _rand_delay(min_s: float = 0.5, max_s: float = 10) -> float:
    return random.uniform(min_s, max_s)


def _ua() -> str:
    pool = [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
        ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    ]
    return random.choice(pool)


def _html_hash(text: str) -> str:
    return hashlib.sha256(text[:4096].encode("utf-8")).hexdigest()[:12]


def _fmt_date(dt) -> str:
    return dt.strftime("%Y-%m-%d")


# ── 核心爬虫 ──────────────────────────────────────────────────────────────

class CtripScraper:
    """携程 Hotel 价格抓取器。

    搜索方式仅用于提取首页列表页（低价采择），
    详细房型价格需后续「行业页」逐页访问（第三阶段）。
    目前是第一阶段（纯列表模式数据归队入 price_log）。
    """

    BASE = "https://hotel.ctrip.com"

    # 城市名 → 携程搜索 URL 编码
    _CITY_REDIRECT = {
        "深圳": "shenzhen",
        "北京市": "beijing",
        "上海": "shanghai",
        "广州": "guangzhou",
        "杭州": "hangzhou",
        "成都": "chengdu",
        "武汉": "wuhan",
        "南京": "nanjing",
        "西安": "xian",
        "重庆": "chongqing",
        "苏州": "suzhou",
        "厦门": "xiamen",
    }

    def __init__(
        self,
        headless: bool = True,
        user_agent: str = "",
        timeout_s: int = 30,
        screenshot_dir: str = "",
        debug_dir: str = "",
    ):
        self.headless = headless
        self.user_agent = user_agent or _ua()
        self.timeout_ms = timeout_s * 1000
        self.screenshot_dir = screenshot_dir
        self.debug_dir = debug_dir
        self._play = None
        self._browser = None
        self._context = None
        self._page: Page | None = None

    async def start(self) -> None:
        self._play = await async_playwright().start()
        self._browser = await self._play.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        self._page = await self._context.new_page()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._context.close()  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            await self._browser.close()  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            self._play.stop()  # type: ignore[attr-defined]

    async def _goto(self, url: str) -> Page:
        assert self._page is not None
        page = self._page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await page.wait_for_timeout(int(_rand_delay(0.5, 3) * 1000))
            return page
        except Exception:
            logger.warning(f"[ctrip] 页面加载失败: {url}")
            return page

    async def _shot(self, tag: str) -> None:
        if not self.screenshot_dir:
            return
        try:
            from pathlib import Path as _path
            _path(self.screenshot_dir).mkdir(parents=True, exist_ok=True)
            await self._page.screenshot(
                path=f"{self.screenshot_dir}/{tag}.png"
            )
        except Exception:
            pass

    # ── 公共入口 ─────────────────────────────────────────────────────────

    async def search_hotels(
        self,
        city: str,
        checkin: str | None = None,
        checkout: str | None = None,
        max_hotels: int = 20,
        stay_delay_min: float | None = None,
        max_retries: int = 3,
    ) -> list[HotelListing]:
        page = self._page

        from datetime import datetime, timedelta
        ci = checkin or _fmt_date(datetime.now() + timedelta(days=14))
        co = checkout or _fmt_date(datetime.now() + timedelta(days=15))

        city_seg = self._CITY_REDIRECT.get(city, city.lower())
        search_url = (
            f"{self.BASE}/hotel/search/{city_seg}"
            f"?cityName={urllib.parse.quote(city)}"
            f"&checkin={ci}&checkout={co}&pagenum=1"
        )
        logger.info(f"[ctrip] 搜索 URL: {search_url}")

        listings: list[HotelListing] = []
        for attempt in range(1, max_retries + 1):
            try:
                await self._goto(search_url)
                await self._shot(f"search_{city}_{attempt}")
                listings = await self._parse_list_page(
                    page, city, ci, co, max_hotels
                )
                logger.info(f"[ctrip] 第{attempt}次，提取 {len(listings)} 家")
                if listings:
                    break
            except Exception as exc:
                logger.warning(f"[ctrip] 第{attempt}次抓取异常: {exc}")
                if attempt >= max_retries:
                    logger.error(f"[ctrip] 全部 {max_retries} 次失败")
                else:
                    time.sleep(random.uniform(2.0, 5.0))

        return listings[:max_hotels]

    async def _parse_list_page(
        self,
        page: Page,
        city: str,
        checkin: str,
        checkout: str,
        max_hotels: int,
    ) -> list[HotelListing]:
        body = await page.inner_text("body", timeout=15000)

        # ── 方案 A：正则兜底（不依赖 DOM 结构）─────────────────────────
        BLOCK_RE = re.compile(
            r"(?P<name>[\u4e00-\u9fff\w\s（）、，.·\-&《》]+?)"
            r"(?:\r?\n|\s)"
            r"(?:(?=\d\.\d星)|)"
            r"(?:(\d(?:\.\d)?)\s*[星级]?\s*)?",
            re.M,
        )
        listings: list[HotelListing] = []
        seen: set[str] = set()

        for match in re.finditer(
            r"(?P<name>[\u4e00-\u9fff][\u4e00-\u9fff\w\s（）、，.·\-&《》]{3,30})"
            r"\s+"
            r"¥\s*(?P<price>[\d,.]+)",
            body,
        ):
            name = match("name").strip()
            # 过滤非酒店名
            # 电话号码、价格、工作有时噪，以名称是否包含特定关键字排除
            # 最简单的是 put 入哈希之后通过验证
            if name in seen:
                continue
            price = float(match("price").replace(",", ""))
            seen.add(name)
            h = HotelListing(
                name=name,
                hotel_id=hashlib.md5(name.encode()).hexdigest()[:10],
                min_price_yuan=price,
                city=city,
                source_url=page.url,
                _raw_html_hash=hashlib.md5(body[:2048].encode()).hexdigest()[:12],
            )
            listings.append(h)
            if len(listings) >= max_hotels:
                break

        # ── 方案 B：DOM 补充（由前已知的结构重建）───
        if len(listings) < max_hotels:
            dom_listings = await self._parse_dom_nodes(page, city, checkin, checkout)
            for dh in dom_listings:
                if dh.name not in seen:
                    seen.add(dh.name)
                    listings.append(dh)
                if len(listings) >= max_hotels:
                    break

        return listings

    async def _parse_dom_nodes(
        self, page: Page, city: str, checkin: str, checkout: str
    ) -> list[HotelListing]:
        results: list[HotelListing] = []
        nodes = await page.query_selector_all("[data-hotelid]")
        logger.info(f"[ctrip] DOM nodes: {len(nodes)}")

        for node in nodes:
            try:
                name_el = await node.query_selector(
                    "[class*='name'], .hotel-name, h3, a"
                )
                name = (
                    (await name_el.inner_text()).strip()
                    if name_el
                    else ""
                )

                # 星级
                star = None
                star_el = await node.query_selector("[class*='star'], [class*='rate']")
                if star_el:
                    m = re.search(r"(\d)(?:\.(\d))?", await star_el.inner_text())
                    if m:
                        star = float(m.group(0))

                # 价格
                price_el = await node.query_selector("[class*='price'], [class*='real']")
                price_txt = (await price_el.inner_text()).strip() if price_el else ""
                price = None
                pm = re.search(r"¥\s*([\d,]+\.?\d*)", price_txt)
                if pm:
                    price = float(pm.group(1).replace(",", ""))

                # 链接 / hotel_id
                link_el = await node.query_selector("a[href]")
                href = await link_el.get_attribute("href") if link_el else ""
                src = href if href.startswith("http") else f"{self.BASE}{href}".replace("//", "/")
                hid = await node.get_attribute("data-hotelid") or ""
                if not hid:
                    hid = hashlib.md5(name.encode()).hexdigest()[:10]

                results.append(
                    HotelListing(
                        name=name,
                        hotel_id=hid,
                        star_rating=star,
                        min_price_yuan=price,
                        city=city,
                        source_url=src,
                        _raw_html_hash="dom",
                    )
                )
            except Exception as exc:
                logger.debug(f"[ctrip] DOM 解析异常: {exc}")

        return results


# ── 对外便利函数 ──────────────────────────────────────────────────────────

async def scrape_ctrip(
    city: str,
    checkin: str | None = None,
    checkout: str | None = None,
    max_hotels: int = 20,
    stay_delay_min: float | None = None,
    max_retries: int = 3,
    **kwargs: Any,
) -> list[HotelListing]:
    scraper = CtripScraper(**kwargs)
    try:
        await scraper.start()
        return await scraper.search_hotels(
            city=city,
            checkin=checkin,
            checkout=checkout,
            max_hotels=max_hotels,
            stay_delay_min=stay_delay_min,
            max_retries=max_retries,
        )
    finally:
        await scraper.close()
