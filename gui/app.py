# -*- coding: utf-8 -*-
"""RateGuard GUI — Streamlit 图形化界面
启动: streamlit run gui/app.py --server.port 8501
"""

import os, sqlite3, io, csv
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB  = ROOT / "db" / "rateguard.db"
LOG = ROOT / "logs" / "rateguard.log"

st.set_page_config(page_title="RateGuard · OTA 价格监控", page_icon="🏨",
                   layout="wide", initial_sidebar_state="expanded")

# ══════════════════════════════════════════════════════
#  helper
# ══════════════════════════════════════════════════════
#  helper
# ══════════════════════════════════════════════════════

@st.cache_data(ttl=30)
def db_load():
    if not DB.exists():
        return pd.DataFrame(), pd.DataFrame()
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT * FROM price_log ORDER BY fetched_at DESC", con)
    try:
        al = pd.read_sql("SELECT * FROM alerts ORDER BY created_at DESC", con)
    except Exception:
        al = pd.DataFrame()
    con.close()
    for col in ("price_yuan",):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "fetched_at" in df.columns:
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], errors="coerce")
        if "created_at" in al.columns:
            al["created_at"] = pd.to_datetime(al["created_at"], errors="coerce")
    return df, al


df, alerts = db_load()
has = not df.empty


# ══════════════════════════════════════════════════════
#  Sidebar
# ══════════════════════════════════════════════════════

with st.sidebar:
    st.title("🏨 RateGuard")
    st.caption("OTA 价格监控 · v0.3")

    page = st.radio("导航", [
        "📊 监控概览",
        "📈 趋势图",
        "🔍 价格对比",
        "📋 历史记录",
        "🚨 价格告警",
    ], label_visibility="collapsed")

    st.divider()
    if has:
        n_hotels   = df["hotel_name"].nunique() if "hotel_name" in df.columns else 0
        n_records  = len(df)
        n_alerts   = len(alerts) if not alerts.empty else 0
        latest = (df["fetched_at"].max().strftime("%m-%d %H:%M")
                  if "fetched_at" in df.columns else "—")

        c1, c2, c3 = st.columns(3)
        c1.metric("酒店", n_hotels)
        c2.metric("记录", n_records)
        c3.metric("告警", n_alerts)
        st.caption(f"最新数据：{latest}")

    st.divider()
    if st.button("🔄 刷新数据", width='stretch'):
        st.cache_data.clear()
        st.rerun()


# ══════════════════════════════════════════════════════
#  Page: 监控概览
# ══════════════════════════════════════════════════════

if page == "📊 监控概览":
    st.markdown("### 📊 监控概览")

    if not has:
        st.info("暂无数据 → 运行 `python3 -m src.main --mode demo` 生成演示数据")
    else:
        # 顶部 KPI
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("监控酒店", df["hotel_name"].nunique())
        k2.metric("价格记录", len(df))
        avg = df["price_yuan"].mean()
        k3.metric("均价", f"¥{avg:.0f}")
        rng = f"¥{df['price_yuan'].min():.0f}～¥{df['price_yuan'].max():.0f}"
        k4.metric("价格区间", rng)
        st.divider()

        col_left, col_right = st.columns([3, 2])

        # 横向柱状图
        with col_left:
            top = (df.groupby("hotel_name", as_index=False)["price_yuan"]
                   .mean().round(0)
                   .sort_values("price_yuan", ascending=True))
            fig_bar = px.bar(top, x="price_yuan", y="hotel_name",
                             orientation="h", labels={
                                 "price_yuan": "均价 (¥)", "hotel_name": ""
                             })
            fig_bar.update_layout(
                height=max(300, 40 * len(top) + 80),
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="均值 (¥)",
            )
            fig_bar.update_traces(marker_color="#2577e3", marker_line_color="#1a5298",
                                  marker_line_width=0.4)
            st.plotly_chart(fig_bar, width='stretch')

        # 后排箱线图
        with col_right:
            fig_box = px.box(df, y="price_yuan", points="outliers",
                             labels={"price_yuan": "价格 (¥)"})
            fig_box.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_box, width='stretch')

        # 近表格
        st.divider()
        st.caption("📋 最近 50 条记录")
        show = df.head(50).copy()
        if "fetched_at" in show.columns:
            show["fetched_at"] = show["fetched_at"].dt.strftime("%Y-%m-%d %H:%M")
        keep = [c for c in ["fetched_at","hotel_name","room_type","price_yuan",
                            "platform","is_sold_out","includes_breakfast"]
                if c in show.columns]
        show = show[keep]
        # 简化列名
        rename = {"fetched_at":"时间","hotel_name":"酒店","room_type":"房型",
                  "price_yuan":"价格(¥)","platform":"平台",
                  "is_sold_out":"售罄","includes_breakfast":"含早"}
        show = show.rename(columns=rename)
        st.dataframe(show, width='stretch', height=350)


# ══════════════════════════════════════════════════════
#  Page: 趋势图
# ══════════════════════════════════════════════════════

elif page == "📈 趋势图":
    st.markdown("### 📈 价格趋势")

    if not has:
        st.info("暂无数据")
    else:
        hotels = sorted(df["hotel_name"].unique())
        default = hotels[: min(5, len(hotels))]
        selected = st.multiselect("选择酒店", hotels, default=default)
        if not selected:
            selected = default

        sub = df[df["hotel_name"].isin(selected)].sort_values("fetched_at")
        fig = go.Figure()
        palette = px.colors.qualitative.Set2
        for i, h in enumerate(selected):
            d = sub[sub["hotel_name"] == h]
            fig.add_trace(go.Scatter(
                x=d["fetched_at"], y=d["price_yuan"],
                mode="lines+markers", name=h,
                line=dict(width=2, color=palette[i % len(palette)]),
                marker=dict(size=5, color=palette[i % len(palette)]),
            ))
        fig.update_layout(
            xaxis_title="时间", yaxis_title="价格 (¥)",
            height=500, hovermode="x unified",
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig, width='stretch')


# ══════════════════════════════════════════════════════
#  Pa  价格对比
# ══════════════════════════════════════════════════════

elif page == "🔍 价格对比":
    st.markdown("### 🔍 价格对比")

    if not has:
        st.info("暂无数据")
    else:
        by_map = {"酒店": "hotel_name", "房型": "room_type", "平台": "platform"}
        by_label = st.radio("分组维度", list(by_map.keys()), horizontal=True)
        by_col = by_map[by_label]

        cA, cB = st.columns(2)
        with cA:
            t1, t2 = st.tabs(["📊 均价柱图", "📦 分布箱图"])
            with t1:
                grp = (df.groupby(by_col, as_index=False)["price_yuan"]
                       .mean().round(0).sort_values("price_yuan", ascending=False))
                fig_a = px.bar(grp, x=by_col, y="price_yuan",
                               labels={"price_yuan": "均价 (¥)", by_col: by_label})
                fig_a.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig_a, width='stretch')
            with t2:
                fig_b = px.box(df, x=by_col, y="price_yuan", points="outliers",
                               labels={"price_yuan": "价格 (¥)", by_col: by_label})
                fig_b.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig_b, width='stretch')

        with cB:
            t3, t4 = st.tabs(["🔥 价格热力", "🥧 占比"])
            with t3:
                pivot = (df.groupby(["fetched_at","hotel_name"], as_index=False)
                         ["price_yuan"].mean())
                pivot = pivot.dropna(subset=["fetched_at","hotel_name"])
                if len(pivot) > 1:
                    pivot_pivot = pivot.pivot(index="fetched_at", columns="hotel_name", values="price_yuan")
                    fig_h = px.imshow(pivot_pivot.T, aspect="auto",
                                      labels=dict(x="日期", y="酒店", color="¥"),
                                      color_continuous_scale="Blues")
                    fig_h.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig_h, width='stretch')
                else:
                    st.info("数据点不足，热力图需要至少 2 天数据")
            with t4:
                dist = (df.groupby("hotel_name")["price_yuan"]
                        .mean().round(0).reset_index()
                        .rename(columns={"price_yuan":"均价"}))
                fig_p = px.pie(dist, names="hotel_name", values="均价",
                               hole=0.4, labels={"hotel_name": "酒店"})
                fig_p.update_traces(textposition="inside", textinfo="label+percent")
                fig_p.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig_p, width='stretch')


# ══════════════════════════════════════════════════════
#  Page: 历史记录
# ══════════════════════════════════════════════════════

elif page == "📋 历史记录":
    st.markdown("### 📋 历史价格记录")
    if not has:
        st.info("暂无数据")
    else:
        # 搜索/ filters
        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            search = st.text_input("搜索酒店名", "", placeholder="输入关键词…")
        with f2:
            room_filter = st.multiselect(
                "房型（不限则可空！）",
                sorted(df["room_type"].dropna().unique()) if "room_type" in df.columns else [])
        with f3:
            min_p = int(df["price_yuan"].min()) if "price_yuan" in df.columns else 0
            max_p = int(df["price_yuan"].max()) if "price_yuan" in df.columns else 9999
            rng_p = st.slider("价格范围", min_p, max_p, (min_p, max_p))

        filtered = df.copy()
        if search and "hotel_name" in filtered.columns:
            filtered = filtered[filtered["hotel_name"].str.contains(search, case=False, na=False)]
        if room_filter and "room_type" in filtered.columns:
            filtered = filtered[filtered["room_type"].isin(room_filter)]
        if "price_yuan" in filtered.columns:
            filtered = filtered[(filtered["price_yuan"] >= rng_p[0]) &
                                (filtered["price_yuan"] <= rng_p[1])]

        st.caption(f"共 **{len(filtered)}** 条记录")
        show = filtered.head(200).copy()
        if "fetched_at" in show.columns:
            show["fetched_at"] = show["fetched_at"].dt.strftime("%Y-%m-%d %H:%M")
        rename = {"fetched_at":"时间","hotel_name":"酒店","room_type":"房型",
                  "price_yuan":"价格(¥)","platform":"平台"}
        show = show[[c for c in rename if c in show.columns]]
        show = show.rename(columns=rename)
        st.dataframe(show, width='stretch', height=460)


# ══════════════════════════════════════════════════════
#  Page: 价格告警
# ══════════════════════════════════════════════════════

elif page == "🚨 价格告警":
    st.markdown("### 🚨 价格告警")

    if not has:
        st.info("暂无数据")
    else:
        # Trigger alerts from low/high quantiles
        latest = df.sort_values("fetched_at").groupby(
            ["hotel_name", "room_type"]).last().reset_index()

        _q10 = latest["price_yuan"].quantile(0.10)
        _q90 = latest["price_yuan"].quantile(0.90)
        triggered = latest[
            (latest["price_yuan"] < _q10) | (latest["price_yuan"] > _q90)
        ].copy()
        triggered["等级"] = triggered["price_yuan"].apply(
            lambda p: "🔴 低价异常" if p < _q10 else "🟠 高价异常"
        )

        k1, k2, k3 = st.columns(3)
        k1.metric("触发数", len(triggered))
        k2.metric("低价阈值", f"¥{_q10:.0f}")
        k3.metric("高价阈值", f"¥{_q90:.0f}")

        st.divider()
        show_cols = ["等级", "hotel_name", "room_type", "price_yuan", "platform"]
        rename_cols = {"等级":"等级","hotel_name":"酒店","room_type":"房型",
                        "price_yuan":"价格(¥)","platform":"平台"}
        avail = [c for c in show_cols if c in triggered.columns]
        display = triggered[avail].rename(columns=rename_cols)
        st.dataframe(display, width='stretch', height=400)

        if not alerts.empty:
            with st.expander("📁 历史告警记录"):
                st.dataframe(alerts, width='stretch')
