"""
dashboard.py — RateGuard static dashboard generator

Outputs static/dashboard.html: single file, inline CSS + Chart.js + data.
Open directly in browser — no server needed.

CDN fallback chain: jsdelivr → unpkg → bootcdn
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("rateguard")
_ROOT   = Path(__file__).resolve().parent.parent
_DB     = str(_ROOT / "db" / "rateguard.db")
_OUT_HTML = _ROOT / "static" / "dashboard.html"


# ── demo seed (forced seed so figures are reproducible across regenerations)

def _seed_demo(conn: sqlite3.Connection, count: int = 60) -> None:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM price_log")
    if cur.fetchone()[0] > 0:
        return
    now = datetime.now()
    templates = [
        ("Hilton Shenzhen",  "Standard", 370),
        ("Hilton Shenzhen",  "Junior Suite", 490),
        ("Hilton Shenzhen",  "Presidential", 880),
        ("Hilton Shenzhen",  "Deluxe",      460),
        ("JW Marriott Chengdu", "King",     310),
        ("JW Marriott Chengdu", "Suite",    440),
        ("JW Marriott Chengdu", "Premier",  380),
        ("JW Marriott Chengdu", "Executive",600),
        ("InterContinental Hangzhou", "Twin",   410),
        ("InterContinental Hangzhou", "Club",   480),
        ("InterContinental Hangzhou", "Ambassador", 620),
        ("Ritz-Carlton Beijing",      "Deluxe",  510),
        ("Ritz-Carlton Beijing",      "Suite",   820),
        ("Ritz-Carlton Beijing",      "Premier", 880),
        ("Ritz-Carlton Beijing",      "Club",    680),
        ("Grand Hyatt Guangzhou",     "Standard",390),
        ("Grand Hyatt Guangzhou",     "Suite",   420),
        ("Grand Hyatt Guangzhou",     "Deluxe",  410),
        ("Park Hyatt Shanghai",       "Standard",500),
        ("Park Hyatt Shanghai",       "Suite",   680),
    ]
    rng = random.Random(42)
    rows = []
    for i in range(count):
        t = rng.choice(templates)
        offset_min = rng.randint(0, 1439)
        ts = now - timedelta(minutes=offset_min)
        price = round(t[2] * rng.uniform(0.80, 1.20), 0)
        rows.append((
            "ctrip", "demo", None,  # platform, run_id, hotel_id
            t[0], t[1], price,
            (now + timedelta(days=rng.randint(1, 30))).strftime("%Y-%m-%d"),
            (now + timedelta(days=rng.randint(2, 31))).strftime("%Y-%m-%d"),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            "", ""
        ))
    conn.executemany(
        "INSERT INTO price_log (platform,run_id,hotel_id,hotel_name,room_type,"
        "price_yuan,checkin,checkout,fetched_at,source_url,raw_html_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()


# ── DB helpers

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
        agg[k]["hotel"]     = r["hotel_name"]
        agg[k]["room_type"] = r["room_type"]
        agg[k]["platform"]  = r.get("platform", "demo")
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


def _peer_avgs(summary: list[dict]) -> dict:
    d: dict[str, list[float]] = {}
    for s in summary:
        d.setdefault(s["hotel"], []).append(float(s["avg"]))
    return {h: round(sum(v) / len(v), 1) for h, v in d.items()}


# ── competitor reference prices  (for vs Market badge / row tint)

_REF: dict[str, float] = {
    "hilton shenzhen":       370,
    "jw marriott chengdu":   310,
    "intercontinental hangzhou": 410,
    "ritz-carlton beijing":  510,
    "grand hyatt guangzhou": 390,
    "park hyatt shanghai":   500,
}


def _ref_price(hotel: str) -> float:
    return _REF.get(hotel.lower(), 360)


# ── table rows

def _table(summary: list[dict]) -> str:
    if not summary:
        return '<tr><td colspan="9"><i>no data yet — run the crawler first</i></td></tr>'
    html = ""
    for s in summary:
        rp     = _ref_price(s["hotel"])
        delta  = s["latest"] - rp
        if delta < -30:
            badge = '<span style="color:#00D2FF;font-size:.72rem">&#9660; low</span>'
        elif delta > 30:
            badge = '<span style="color:#FF6B6B;font-size:.72rem">&#9650; high</span>'
        else:
            badge = '<span style="color:#8Be268;font-size:.72rem">&#9679; match</span>'
        diff_pct = (s["avg"] - rp) / rp * 100
        if diff_pct < -20:
            bg = "style='background:rgba(0,210,255,.035)'"
        elif diff_pct > 20:
            bg = "style='background:rgba(255,107,107,.035)'"
        else:
            bg = ""
        html += (
            f"<tr {bg}>"
            f"<td><b>{_esc(s['hotel'])}</b></td>"
            f"<td>{_esc(s['room_type'])}</td>"
            f"<td>{_esc(s['platform'])}</td>"
            f"<td style='text-align:center;color:#888'>{s['samples']}</td>"
            f"<td style='text-align:right;font-weight:600'>¥{s['avg']:.0f}</td>"
            f"<td style='text-align:right;color:#787878'>¥{s['min']:.0f}</td>"
            f"<td style='text-align:right;color:#787878'>¥{s['max']:.0f}</td>"
            f"<td style='text-align:right;font-weight:700'>{s['latest']:.0f}</td>"
            f"<td>{badge}</td>"
            f"</tr>"
        )
    return html


def _esc(s: str) -> str:
    import html as _h
    return _h.escape(s)


# ── main entry

def generate(days: int = 3, out: str | None = None) -> Path:
    now   = datetime.now()
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    end   = now.strftime("%Y-%m-%dT%H:%M:%S")

    rows    = _rows(start, end)
    summary = _venue_summary(rows)

    # seed demo data if DB is empty
    if not rows:
        conn = sqlite3.connect(_DB)
        _seed_demo(conn)
        rows    = _rows(start, end)
        summary = _venue_summary(rows)
        conn.close()

    trend   = _trend(rows)
    peers   = _peer_avgs(summary)

    p_html  = _table(summary)
    now_str = now.strftime("%Y-%m-%d %H:%M")

    #  KPIs
    p_avgs      = list(peers.values())
    avg_price   = round(sum(p_avgs) / len(p_avgs), 1) if p_avgs else 0
    low_k       = min(p_avgs) if p_avgs else 0
    high_k      = max(p_avgs) if p_avgs else 0

    #  Chart configs as JSON (these contain { — so we f-string them,
    #    NOT pass through .format() which would re-parse them)
    trend_cfg  = json.dumps(_make_trend_cfg(trend["labels"], trend["avgs"]))
    radar_cfg  = json.dumps(_make_radar_cfg(peers))

    html = (
        _HEAD +
        _CSS +
        _BODY_HEAD.format(now=now_str, days=days,
                          hotels=len(peers), records=len(summary),
                          avg=avg_price, low=low_k, high=high_k) +
        _CHARTS + "\n" +
        _TABLE_HEAD +
        p_html +
        _TABLE_FOOT +
        _FOOTER_SCRIPT +
        _CDN_CALLBACK.replace("__TREND_JSON__", trend_cfg)
                       .replace("__RADAR_JSON__", radar_cfg) +
        "</body></html>"
    )

    out_path = Path(out) if out else _OUT_HTML
    out_path.write_text(html, encoding="utf-8")
    logger.info("[dashboard] -> %s   (%dB)", out_path, out_path.stat().st_size)
    return out_path


# ── chart config builders ────────────────────────────────────────────────

def _make_trend_cfg(labels: list[str], vals: list[float]) -> dict:
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "avg yen",
                "data": vals,
                "borderColor": "#6C63FF",
                "backgroundColor": "rgba(108,99,255,.1)",
                "fill": True, "tension": 0.40, "pointRadius": 4,
                "pointHoverRadius": 7,
                "pointBackgroundColor": "#6C63FF",
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2,
            }]
        },
        "options": {
            "plugins": {"legend": {"display": False},
                        "tooltip": {"mode": "index", "intersect": False}},
            "scales": {
                "x": {"grid": {"color": "#1e1e2e", "display": False},
                      "ticks": {"color": "#7a7a8a", "maxRotation": 0, "maxTicksLimit": 12}},
                "y": {"grid": {"color": "#2a2a3e"},
                      "ticks": {"color": "#7a7a8a"},
                      "suggestedMin": 150, "suggestedMax": 950}
            },
            "interaction": {"mode": "index", "intersect": False},
            "responsive": True, "maintainAspectRatio": False,
        },
        "plugins": [{
            "id": "lineDot",
            "afterDraw": (
                "function(chart) {"
                "var ctx = chart.ctx, canvas = chart.canvas;"
                "ctx.save(); ctx.fillStyle = '#6C63FF'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'center';"
                "chart.data.datasets[0].data.forEach(function(v, i) {"
                "var meta = chart.getDatasetMeta(0);"
                "if (!meta.data[i]) return;"
                "var x = meta.data[i].x, y = meta.data[i].y;"
                "ctx.fillText(v, x, y - 10);"
                "}); ctx.restore();"
                "}"
            )
        }]
    }


def _make_radar_cfg(peers: dict) -> dict:
    names = list(peers.keys())
    avgs  = list(peers.values())
    return {
        "type": "bar",
        "data": {
            "labels": names,
            "datasets": [{
                "label": "avg yen",
                "data": avgs,
                "backgroundColor": "rgba(0,210,255,.50)",
                "borderColor": "#00D2FF",
                "borderWidth": 1.5,
                "borderRadius": 4,
            }]
        },
        "options": {
            "indexAxis": "y",
            "plugins": {"legend": {"display": False}},
            "scales": {
                "x": {"grid": {"color": "#2a2a3e"},
                      "ticks": {"color": "#888", "callback": "function(v) { return 'yen' + v; }"}},
                "y": {"grid": {"display": False},
                      "ticks": {"color": "#aaa"}},
            },
            "responsive": True, "maintainAspectRatio": False,
        },
        "plugins": [{
            "id": "barLabels",
            "afterDatasetsDraw": (
                "function(chart) {"
                "var meta = chart.getDatasetMeta(0);"
                "meta.data.forEach(function(bar, i) {"
                "var v = chart.data.datasets[0].data[i];"
                "if (typeof v !== 'number') return;"
                "var ctx = chart.ctx, label = 'yen' + Math.round(v), x = bar.x + 6, y = bar.y + bar.height / 2 + 4;"
                "ctx.save(); ctx.fillStyle = '#b49fff'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';"
                "ctx.fillText(label, x, y); ctx.restore();"
                "});"
                "}"
            )
        }]
    }


# ── HTML fragments ───────────────────────────────────────────────────────
# Each fragment is a separate string; the only .format() tokens are {now},
# {days}, {hotels}, {records}, {avg}, {low}, {high} — nothing else clashes.

_HEAD = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' \
        '<meta name="viewport" content="width=device-width,initial-scale=1">' \
        '<title>RateGuard Dashboard</title></head><body>'


_CSS = """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background:#0f1117;color:#e4e4e7;min-height:100vh}
.top{background:linear-gradient(135deg,#6C63FF,#00D2FF,#FF6B6B);
  padding:28px 36px;color:#fff;display:flex;align-items:flex-end;justify-content:space-between;gap:14px}
.top h1{font-size:1.9rem;font-weight:700;letter-spacing:-.02em}
.top .sub{opacity:.8;font-size:.82rem;margin-top:5px}
.refresh-btn{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
  color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer;font-size:.78rem;
  transition:background .15s}
.refresh-btn:hover{background:rgba(255,255,255,.25)}
.kpis{display:grid;
  grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
  gap:12px;padding:20px 30px}
.kpi{background:#1a1a2e;border-radius:14px;padding:18px 22px;
  border:1px solid #252535;transition:border-color .2s}
.kpi:hover{border-color:#3a3a5a}
.kpi .v{font-size:2rem;font-weight:700;
  background:linear-gradient(135deg,#6C63FF,#00D2FF);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.kpi .l{font-size:.72rem;color:#666;margin-top:3px}
.charts{display:grid;grid-template-columns:2fr 1fr;gap:12px;padding:0 30px 20px}
@media(max-width:960px){.charts{grid-template-columns:1fr}}
.cb{background:#1a1a2e;border-radius:14px;padding:18px 20px;border:1px solid #252535}
.cb h3{font-size:.78rem;color:#666;margin-bottom:12px;text-transform:uppercase;
  letter-spacing:.06em}
table{width:100%;border-collapse:collapse;
  background:#1a1a2e;border-radius:12px;overflow:hidden;border:1px solid #252535}
th{background:#222240;color:#666;font-size:.68rem;text-transform:uppercase;
  letter-spacing:.05em;padding:9px 12px;text-align:left}
td{padding:10px 12px;border-bottom:1px solid #222240;font-size:.80rem}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1e1e36}
.tbl{padding:0 30px 24px}
footer{text-align:center;padding:14px;color:#444;font-size:.72rem}
.no-data{color:#555;text-align:center;padding:24px;font-style:italic}
.cdns{position:fixed;bottom:6px;right:10px;font-size:.65rem;color:#3334}
</style>"""

_BODY_HEAD = """<div class="top">
<h1>RateGuard  Dashboard</h1>
<div class="sub">{now}  &middot;  last {days}d</div>
<button class="refresh-btn" onclick="location.reload()">&#x21bb; reload</button>
</div>
<div class="kpis">
  <div class="kpi"><div class="v">{hotels}</div><div class="l">hotels monitored</div></div>
  <div class="kpi"><div class="v">{records}</div><div class="l">price records</div></div>
  <div class="kpi"><div class="v">yen{avg:.0f}</div><div class="l">market avg</div></div>
  <div class="kpi"><div class="v">yen{low:.0f} &ndash; yen{high:.0f}</div><div class="l">price range</div></div>
</div>"""

_CHARTS = """<div class="charts">
  <div class="cb"><h3>Price Trend (hourly avg)</h3>
    <canvas id="tr" height="200" style="max-height:320px"></canvas>
  </div>
  <div class="cb"><h3>Avg by Hotel</h3>
    <canvas id="radar" height="200" style="max-height:320px"></canvas>
  </div>
</div>"""

_TABLE_HEAD = """<div class="tbl">
<table>
<thead><tr>
  <th>Hotel</th><th>Room Type</th><th>Platform</th><th>Samples</th>
  <th>Avg (yen)</th><th>Low (yen)</th><th>High (yen)</th><th>Latest (yen)</th><th>vs Market</th>
</tr></thead>
<tbody>"""

_TABLE_FOOT = """</tbody></table></div>"""

_FOOTER_SCRIPT = """<footer>RateGuard v1.0 &nbsp;|&nbsp; auto-refresh 30s</footer>
<div class="cdns">CDN: try1 jsdelivr &rarr; 2 unpkg &rarr; 3 bootcdn</div>
<script>
(function(){
var urls=[
'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js',
'https://unpkg.com/chart.js@4/dist/chart.umd.min.js',
'https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.0/chart.umd.min.js'
];
var loaded=false, i=0;
function nxt(){
  var s=document.createElement('script');
  s.src=urls[i++]; s.async=true;
  s.onerror=nxt;
  s.onload=function(){loaded=true; render()};
  document.head.appendChild(s);
}
function render(){
  try{
    var tc=document.getElementById('tr');
    var rc=document.getElementById('radar');
    var tctx=tc.getContext('2d');
    var rctx=rc.getContext('2d');
    new Chart(tctx, JSON.parse(document.getElementById('_tc').textContent));
    new Chart(rctx, JSON.parse(document.getElementById('_rc').textContent));
  }catch(e){
    var c=document.querySelector('.charts');
    c.insertAdjacentHTML('beforeend',
      '<p style="color:FF6B6B;text-align:center;padding:12px">Chart.js failed to load. '
      +'Try a different network.</p>');
  }
}
setTimeout(function(){if(!loaded)nxt()},1200);
setTimeout(render,1500);
setInterval(function(){
  var t=document.title, m=t.match(/\d+/);
  if(!m)document.title='RateGuard  30s'; else
    document.title='RateGuard  '+(parseInt(m[0])-1)+'s';
  if(m&&parseInt(m[0])<=1)location.reload();
},1000);
})();
</script>"""


_CDN_CALLBACK = (
    '<script type="application/json" id="_tc">'
    '__TREND_JSON__</script>'
    '<script type="application/json" id="_rc">'
    '__RADAR_JSON__</script>'
    "</body></html>"
)


def _make_trend_plugin_cfg() -> None:  # placeholder (plugin cfg built inline in _make_trend_cfg)
    pass


def _radar_plugin_cfg() -> None:  # placeholder
    pass
