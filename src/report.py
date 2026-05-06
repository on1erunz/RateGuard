"""
report.py — RateGuard 价格报告生成器

支持：日报 / 周报 HTML 单文件 + Chart.js 图表，无需构建步骤。
"""

from __future__ import annotations

import csv
import html
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("rateguard")

_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _ROOT / "db" / "rateguard.db"
_REPORT_DIR = _ROOT / "reports"
_REPORT_DIR.mkdir(exist_ok=True)


def _fetch_rows(start: str, end: str, platform: str | None = None) -> list[dict]:
    if not _DB_PATH.exists():
        return []
    sql = "SELECT platform,hotel_name,hotel_id,room_type,price_yuan,checkin,checkout,is_sold_out,includes_breakfast,fetched_at,source_url,raw_html_hash FROM price_log WHERE fetched_at BETWEEN ? AND ?"
    params = [start, end]
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    sql += " ORDER BY fetched_at DESC"
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _venue_summary(rows: list[dict]) -> list[dict]:
    from collections import defaultdict
    agg: dict = {}
    for r in rows:
        key = (r["hotel_name"], r["room_type"])
        if key not in agg:
            agg[key] = {"count": 0, "total": 0.0,
                        "min": float("inf"), "max": 0.0, "prices": []}
        p = r.get("price_yuan")
        if p is not None:
            q = float(p)
            agg[key]["count"] += 1
            agg[key]["total"] += q
            agg[key]["min"] = min(agg[key]["min"], q)
            agg[key]["max"] = max(agg[key]["max"], q)
            agg[key]["prices"].insert(0, q)
    result = []
    for (name, rtype), v in sorted(agg.items()):
        avg = v["total"] / v["count"] if v["count"] else 0
        result.append({
            "hotel_name": name, "room_type": rtype, "samples": v["count"],
            "avg_price": round(avg, 1),
            "min_price": round(v["min"], 1) if v["min"] != float("inf") else None,
            "max_price": round(v["max"], 1),
            "latest_price": round(v["prices"][0], 1) if v["prices"] else None,
        })
    return result


def export_csv(start: str, end: str, platform: str | None = None) -> Path | None:
    rows = _fetch_rows(start, end, platform)
    if not rows:
        return None
    import csv
    out = _REPORT_DIR / f"prices_{start}_{end}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["fetched_at","platform","hotel_name",
                           "room_type","price_yuan","checkin",
                           "checkout","source_url"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"[report] CSV -> {out}  ({len(rows)} rows)")
    return out


def generate_html_report(
        period: str = "daily",
        platform: str | None = None,
        report_dir: str | None = None) -> Path | None:
    now = datetime.now()
    if period == "daily":
        start = (now - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S")
        title = f"{now.strftime('%Y-%m-%d')} daily"
    else:
        start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S")
        title = f"{now.strftime('%Y-%m-%d')} weekly"

    rows = _fetch_rows(start, end, platform)
    summary = _venue_summary(rows)
    if not summary:
        logger.warning("[report] no data")
        return None

    total_hotels = len({s["hotel_name"] for s in summary})
    total_records = sum(s["samples"] for s in summary)
    avg_price = sum(s.get("avg_price", 0) for s in summary) / max(len(summary), 1)
    ref = _market_ref("深圳", "标准间") or 400.0
    alert_count = sum(
        1 for s in summary
        if abs((s.get("latest_price") or 0) - ref) / ref > 0.30)

    tr = ""
    for s in summary:
        tag = _trend_tag(s.get("latest_price"), ref)
        tr += (f"<tr><td>{html.escape(s['hotel_name'])}</td>"
               f"<td>{html.escape(s['room_type'])}</td>"
               f"<td>{s['samples']}</td>"
               f"<td>yen{s.get('avg_price', '--'):.0f}</td>"
               f"<td>yen{s.get('min_price', '--'):.0f}</td>"
               f"<td>yen{s.get('max_price', '--'):.0f}</td>"
               f"<td>{tag}</td></tr>\n")

    cjs = _build_chart(rows)
    out_dir = Path(report_dir) if report_dir else _REPORT_DIR
    out = out_dir / f"rateguard_{period}_{now.strftime('%Y%m%d')}.html"
    out.write_text(_HTML.format(
        title=title, total_hotels=total_hotels, total_records=total_records,
        avg_price=avg_price, alert_count=alert_count, hotel_rows=tr,
        buckets=12, chart_data=cjs,
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S")), encoding="utf-8")
    logger.info(f"[report] HTML -> {out}")
    return out


def _trend_tag(price, ref):
    if price is None or ref == 0:
        return '<span class="tag tag-neutral">—</span>'
    d = (price - ref) / ref * 100
    if d < -10:
        return f'<span class="tag tag-down">▼ {d:+.0f}%</span>'
    if d > 10:
        return f'<span class="tag tag-up">▲ {d:+.0f}%</span>'
    return '<span class="tag tag-neutral">flat</span>'


def _price_buckets(rows):
    by_hour = {}
    for r in rows:
        t = r.get("fetched_at") or ""
        try:
            ts = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
            k = ts.strftime("%H:00")
        except Exception:
            continue
        p = r.get("price_yuan")
        if p is not None:
            by_hour.setdefault(k, []).append(float(p))
    return dict(sorted(by_hour.items()))


def _build_chart(rows):
    buckets = _price_buckets(rows)
    labels = list(buckets.keys())
    avgs = [round(sum(v)/len(v), 1) if v else 0 for v in buckets.values()]
    d = json.dumps({"labels": labels, "avgs": avgs})
    return ("const tc=document.getElementById('trend').getContext('2d');"
            f"const td={d};"
            "new Chart(tc,{type:'line',data:{labels:td.labels,datasets:[{"
            "label:'avg yen',data:td.avgs,borderColor:'#6C63FF',"
            "backgroundColor:'rgba(108,99,255,.08)',fill:true,tension:.35,"
            "pointRadius:3,pointBackgroundColor:'#6C63FF'}]},"
            "options:{plugins:{legend:{display:false}},"
            "scales:{x:{grid:{display:false}},"
            "y:{grid:{color:'#f0f0f0'},beginAtZero:false}}}});")


_MARKET_REF = {
    "深圳": {"经济房": 250, "标准间": 370, "高级大床房": 460, "豪华套房": 880},
    "北京市": {"经济房": 320, "标准间": 510, "高级大床房": 720, "豪华套房": 1350},
    "上海": {"经济房": 310, "标准间": 500, "高级大床房": 680, "豪华套房": 1250},
    "广州": {"经济房": 260, "标准间": 390, "高级大床房": 410, "豪华套房": 960},
}

def _market_ref(city, room_type):
    if not city:
        return None
    m = _MARKET_REF.get(city) or _MARKET_REF.get(city[:2], {})
    return m.get(room_type)


_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RateGuard {title}</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f7fa;color:#1a1a2e;line-height:1.6}}
.header{{background:linear-gradient(135deg,#6C63FF,#00D2FF);color:#fff;padding:28px 32px}}
.header h1{{font-size:1.5rem}} .header .sub{{opacity:.85;font-size:.9rem;margin-top:4px}}
.section{{padding:20px 32px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 2px 8px rgba(0,0,0,.06);text-align:center}}
.kpi .v{{font-size:1.8rem;font-weight:700;color:#6C63FF}} .kpi .l{{font-size:.8rem;color:#888;margin-top:2px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
th{{background:#f0f0ff;color:#555;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;padding:10px 14px;text-align:left}}
td{{padding:10px 14px;border-top:1px solid #f0f0f0;font-size:.88rem}} tr:hover td{{background:#f9f9ff}}
.tag{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.72rem;font-weight:600}}
.tag-up{{background:#fde8e8;color:#c0392b}} .tag-down{{background:#e8f8f0;color:#1e8449}} .tag-neutral{{background:#f0f0f0;color:#666}}
.footer{{text-align:center;color:#bbb;font-size:.75rem;padding:20px}} canvas{{max-width:100%}}</style>
</head>
<body>
<div class="header"><h1>RateGuard price report</h1><div class="sub">{title}</div></div>
<div class="section">
<div class="kpi-grid">
<div class="kpi"><div class="v">{total_hotels}</div><div class="l">hotels</div></div>
<div class="kpi"><div class="v">{total_records}</div><div class="l">records</div></div>
<div class="kpi"><div class="v">yen{avg_price:.0f}</div><div class="l">avg</div></div>
<div class="kpi"><div class="v">{alert_count}</div><div class="l">alerts</div></div>
</div>
<h2 style="margin-bottom:12px;font-size:1rem">hotel prices</h2>
<table><thead><tr><th>Hotel</th><th>Room</th><th>Samples</th><th>Avg</th><th>Low</th><th>High</th><th>vs ref</th></tr></thead>
<tbody>{hotel_rows}</tbody></table>
</div>
<div class="section">
<h2 style="margin-bottom:12px;font-size:1rem">trend ({buckets} buckets)</h2>
<canvas id="trend" height="220"></canvas></div>
<div class="footer">RateGuard v1.0   {generated_at}</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>{chart_data}</script>
</body></html>"""
