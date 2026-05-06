"""
dashboard.py — RateGuard static dashboard generator

Outputs static/dashboard.html: single file, inline CSS + Chart.js + data.
Open directly in browser – no server needed.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("rateguard")
_ROOT    = Path(__file__).resolve().parent.parent
_DB      = str(_ROOT / "db" / "rateguard.db")
OUT_FILE = _ROOT / "static" / "dashboard.html"


# ── database ────────────────────────────────────────────────────────────

def _rows(start: str, end: str) -> list[dict]:
    if not Path(_DB).exists():
        return []
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT hotel_name,room_type,price_yuan,checkin,platform,fetched_at "
        "FROM price_log WHERE fetched_at BETWEEN ? AND ? ORDER BY fetched_at DESC",
        [start, end]).fetchall()]
    conn.close()
    return rows


def _venue_summary(rows: list[dict]) -> list[dict]:
    from collections import defaultdict
    agg: dict = defaultdict(lambda: {"prices": []})
    for r in rows:
        k = r["hotel_name"] + "|" + r["room_type"]
        agg[k]["hotel"]      = r["hotel_name"]
        agg[k]["room_type"]  = r["room_type"]
        agg[k]["platform"]   = r.get("platform", "demo")
        p = r.get("price_yuan")
        if p is not None:
            agg[k]["prices"].append(float(p))
    result = []
    for v in agg.values():
        if v["prices"]:
            ps = sorted(v["prices"])
            result.append({
                "hotel": v["hotel"], "room_type": v["room_type"],
                "platform": v["platform"],
                "latest": ps[-1],
                "avg":    round(sum(ps) / len(ps), 1),
                "min":    ps[0], "max": ps[-1],
                "samples": len(ps),
            })
    return sorted(result, key=lambda x: (x["hotel"], x["room_type"]))


def _trend(rows: list[dict]) -> dict:
    buckets: dict[str, list[float]] = {}
    for r in rows:
        t = r.get("fetched_at", "")
        try:
            ts = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
            k = ts.strftime("%H:00")
            p = r.get("price_yuan")
            if p is not None:
                buckets.setdefault(k, []).append(float(p))
        except Exception:
            pass
    labels = sorted(buckets.keys())
    avgs   = [round(sum(buckets[k]) / len(buckets[k]), 1) if buckets[k] else 0 for k in labels]
    return {"labels": labels, "avgs": avgs}


def _peer_avgs(rows: list[dict]) -> dict:
    agg: dict[str, list] = {}
    for r in rows:
        p = r.get("price_yuan")
        if p is not None:
            agg.setdefault(r["hotel_name"], []).append(float(p))
    return {h: round(sum(v) / len(v), 1) for h, v in agg.items()}


# ── table rows ──────────────────────────────────────────────────────────

def _table(summary: list[dict]) -> str:
    if not summary:
        return '<tr><td colspan="8"><i>no data yet — run the crawler first</i></td></tr>'
    html = ""
    for s in summary:
        html += (
            f"<tr>"
            f"<td>{_esc(s['hotel'])}</td>"
            f"<td>{_esc(s['room_type'])}</td>"
            f"<td>{_esc(s['platform'])}</td>"
            f"<td>{s['samples']}</td>"
            f"<td>¥{s['avg']:.0f}</td>"
            f"<td>¥{s['min']:.0f}</td>"
            f"<td>¥{s['max']:.0f}</td>"
            f"<td>¥{s['latest']:.0f}</td>"
            f"</tr>\n"
        )
    return html


def _esc(s: str) -> str:
    import html as _h
    return _h.escape(s)


# ── main entry ──────────────────────────────────────────────────────────

def generate(days: int = 3, out: str | None = None) -> Path:
    now   = datetime.now()
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    end   = now.strftime("%Y-%m-%dT%H:%M:%S")

    rows    = _rows(start, end)
    summary = _venue_summary(rows)
    trend   = _trend(rows)
    peers   = _peer_avgs(rows)

    p_html   = _table(summary)
    p_names  = list(peers.keys())
    p_avgs   = list(peers.values())
    p_labels = json.dumps(trend["labels"])
    p_data   = json.dumps(trend["avgs"])
    now_str  = now.strftime("%Y-%m-%d %H:%M")

    # .format() can only safely fill {NAME} tokens.
    # JS chart data (JSON arrays) we inject via safe tokens → regex replace.
    raw = _TMPL.format(
        generated_at = now_str,
        total_hotels = len(peers),
        total_records= len(summary),
        avg_price    = round(sum(p_avgs) / len(p_avgs), 1) if p_avgs else 0,
        low          = min(p_avgs) if p_avgs else 0,
        high         = max(p_avgs) if p_avgs else 0,
        p_rows       = p_html,
        days         = days,
    )
    # inject chart data
    raw = raw.replace("__PEER_NAMES__", json.dumps(p_names, ensure_ascii=False))
    raw = raw.replace("__PEER_AVGS__",  json.dumps(p_avgs, ensure_ascii=False))
    raw = raw.replace("__TREND_LABELS__", p_labels)
    raw = raw.replace("__TREND_DATA__",   p_data)

    out_path = Path(out) if out else OUT_FILE
    out_path.write_text(raw, encoding="utf-8")
    logger.info(f"[dashboard] -> {out_path}  ({out_path.stat().st_size}B)")
    return out_path
    return out_path


_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RateGuard Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;background:#0f1117;color:#e4e4e7;min-height:100vh}}
.top{{background:linear-gradient(135deg,#6C63FF,#00D2FF,#FF6B6B);padding:28px 36px;color:#fff}}
.top h1{{font-size:1.9rem;font-weight:700;letter-spacing:-.02em}}
.top .sub{{opacity:.8;font-size:.85rem;margin-top:4px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;padding:22px 32px}}
.kpi{{background:#1e1e2e;border-radius:14px;padding:18px 22px;border:1px solid #2a2a3e}}
.kpi .v{{font-size:2rem;font-weight:700;background:linear-gradient(135deg,#6C63FF,#00D2FF);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.kpi .l{{font-size:.75rem;color:#888;margin-top:2px}}
.charts{{display:grid;grid-template-columns:2fr 1fr;gap:14px;padding:0 32px 22px}}
@media(max-width:900px){{.charts{{grid-template-columns:1fr}}}}
.cb{{background:#1e1e2e;border-radius:14px;padding:20px 22px;border:1px solid #2a2a3e}}
.cb h3{{font-size:.9rem;color:#888;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;background:#1e1e2e;border-radius:14px;overflow:hidden;border:1px solid #2a2a3e}}
th{{background:#252535;color:#888;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;padding:9px 12px;text-align:left}}
td{{padding:10px 12px;border-bottom:1px solid #2a2a3e;font-size:.82rem}}
tr:hover td{{background:#262640}}
.tbl{{padding:0 32px 24px}}
footer{{text-align:center;padding:16px;color:#555;font-size:.75rem}}
.meta{{text-align:right;font-size:.75rem;color:#555}}
</style>
</head>
<body>

<div class="top">
  <h1>RateGuard  Dashboard</h1>
  <div class="sub">Generated at {generated_at}  ·  Range: last {days} days</div>
  <div class="meta"><button onclick="window.location.reload()">&#x21bb; refresh</button></div>
</div>

<div class="kpis">
  <div class="kpi"><div class="v">{total_hotels}</div><div class="l">monitored hotels</div></div>
  <div class="kpi"><div class="v">{total_records}</div><div class="l">price records</div></div>
  <div class="kpi"><div class="v">¥{avg_price:.0f}</div><div class="l">market avg (¥)</div></div>
  <div class="kpi"><div class="v">¥{low:.0f} – ¥{high:.0f}</div><div class="l">price range</div></div>
</div>

<div class="charts">
  <div class="cb">
    <h3>Price Trend</h3>
    <canvas id="tr" height="180"></canvas>
  </div>
  <div class="cb">
    <h3>Average by Hotel</h3>
    <canvas id="radar" height="180"></canvas>
  </div>
</div>

<div class="tbl">
<table>
<thead><tr>
  <th>Hotel</th><th>Room Type</th><th>Platform</th><th>Samples</th>
  <th>Avg (¥)</th><th>Low (¥)</th><th>High (¥)</th><th>Latest (¥)</th>
</tr></thead>
<tbody>
{p_rows}
</tbody>
</table>
</div>

<footer>RateGuard v1.0  |  30s auto-refresh</footer>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
// Trend
const tx = document.getElementById('tr').getContext('2d');
new Chart(tx, {{
  type: 'line',
  data: {{
    labels: __TREND_LABELS__,
    datasets: [{{
      label: 'avg yen',
      data: __TREND_DATA__,
      borderColor: '#6C63FF',
      backgroundColor: 'rgba(108,99,255,.08)',
      fill: true, tension: .35, pointRadius: 3
    }}]
  }},
  options: {{
    plugins: {{legend:{{display:false}}}},
    scales: {{
      x: {{grid:{{display:false}}}},
      y: {{grid:{{color:'#2a2a3e'}}, ticks:{{color:'#666'}}}}
    }}
  }}
}});

// Hotel avg bar
const rc = document.getElementById('radar').getContext('2d');
new Chart(rc, {{
  type: 'bar',
  data: {{
    labels: __PEER_NAMES__,
    datasets: [{{
      label: 'avg yen',
      data: __PEER_AVGS__,
      backgroundColor: 'rgba(0,210,255,.45)',
      borderColor: '#00D2FF',
      borderWidth: 1
    }}]
  }},
  options: {{
    indexAxis: 'y',
    plugins: {{legend:{{display:false}}}},
    scales: {{
      x: {{grid:{{color:'#2a2a3e'}}, ticks:{{color:'#666'}}}},
      y: {{grid:{{display:false}}, ticks:{{color:'#aaa'}}}}
    }}
  }}
}});

// Countdown
let r=30;
setInterval(function(){{r--;document.title='RateGuard  '+r+'s';
if(r<=0)window.location.reload()}},1000);
</script>
</body></html>"""