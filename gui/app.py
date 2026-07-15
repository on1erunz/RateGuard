# -*- coding: utf-8 -*-
"""RateGuard 本地看板。

启动：streamlit run gui/app.py --server.port 8501
仅展示本项目实际采集到的酒店、房型和价格；不生成或混入示例数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "db" / "rateguard.db"

STATUS_LABELS = {
    "available": "可售",
    "sold_out": "售罄",
    "manual_review": "待人工核验",
    "price_hidden": "价格待核验",
    "error": "采集异常",
}

st.set_page_config(page_title="RateGuard 酒店价格监控", page_icon="🏨", layout="wide")


@st.cache_data(ttl=30)
def load_observations() -> pd.DataFrame:
    """Read only the room-level observations collected by this MVP."""
    if not DB.exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(DB) as connection:
            frame = pd.read_sql_query(
                """
                SELECT run_id, hotel_id, hotel_name, room_id, room_name,
                       rate_plan_key, checkin, checkout,
                       price_yuan, previous_price_yuan, price_delta_yuan,
                       is_available, status, detail, raw_json_path,
                       html_path, screenshot_path, fetched_at
                FROM ctrip_mvp_observations
                ORDER BY fetched_at DESC
                """,
                connection,
            )
    except Exception:
        return pd.DataFrame()

    if frame.empty:
        return frame
    frame["platform"] = "携程"
    for column in ("price_yuan", "previous_price_yuan", "price_delta_yuan"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["fetched_at"] = pd.to_datetime(frame["fetched_at"], errors="coerce")
    # The collector persists ISO dates (for example 2026-07-14); accepting the
    # generic parser also keeps the dashboard compatible with compact YYYYMMDD.
    frame["checkin_date"] = pd.to_datetime(frame["checkin"], errors="coerce")
    frame["状态"] = frame["status"].map(STATUS_LABELS).fillna(frame["status"])
    return frame


def latest_by_room(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the newest result for one hotel / room plan / stay date."""
    keys = ["platform", "hotel_id", "room_id", "rate_plan_key", "checkin"]
    return (
        frame.sort_values("fetched_at")
        .drop_duplicates(keys, keep="last")
        .sort_values(["hotel_name", "room_name", "price_yuan"], na_position="last")
    )


def display_table(frame: pd.DataFrame) -> pd.DataFrame:
    view = frame.copy()
    view["入住日期"] = view["checkin_date"].dt.strftime("%Y-%m-%d").fillna(view["checkin"])
    view["当前价格（元/晚）"] = view["price_yuan"]
    view["上一轮价格（元/晚）"] = view["previous_price_yuan"]
    view["变动（元）"] = view["price_delta_yuan"]
    view["更新时间"] = view["fetched_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
    view["房型"] = view["room_name"].fillna("未识别房型")
    columns = [
        "platform", "hotel_name", "入住日期", "房型",
        "当前价格（元/晚）", "上一轮价格（元/晚）", "变动（元）", "状态", "更新时间",
    ]
    return view[columns].rename(columns={"platform": "平台", "hotel_name": "酒店"})


df = load_observations()

with st.sidebar:
    st.title("🏨 RateGuard")
    st.caption("酒店竞对房型价格监控")
    page = st.radio("页面", ["当前价格", "价格历史", "采集状态"], label_visibility="collapsed")
    if st.button("刷新数据", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if df.empty:
    st.title("酒店价格监控")
    st.info("尚无实际采集记录。完成一次携程采集后，这里会显示房型、每晚价格和变动情况。")
    st.stop()

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    platforms = st.multiselect("平台", sorted(df["platform"].unique()), default=sorted(df["platform"].unique()))
with filter_col2:
    hotels = st.multiselect("酒店", sorted(df["hotel_name"].unique()), default=sorted(df["hotel_name"].unique()))
with filter_col3:
    dates = sorted(df["checkin_date"].dropna().dt.date.unique())
    selected_dates = st.multiselect("入住日期", dates, default=dates)

filtered = df[
    df["platform"].isin(platforms)
    & df["hotel_name"].isin(hotels)
    & df["checkin_date"].dt.date.isin(selected_dates)
].copy()

if filtered.empty:
    st.warning("当前筛选条件下没有采集记录。")
    st.stop()

latest = latest_by_room(filtered)

if page == "当前价格":
    st.title("当前可售房型价格")
    st.caption("按酒店、房型和价格计划取最新一轮结果；价格变动达到 10 元时会触发飞书通知。")

    available = latest[latest["status"] == "available"]
    changed = latest[latest["price_delta_yuan"].abs() >= 10]
    sold_or_review = latest[latest["status"].isin(["sold_out", "manual_review", "price_hidden", "error"])]
    metric_a, metric_b, metric_c = st.columns(3)
    metric_a.metric("已监测酒店", latest["hotel_id"].nunique())
    metric_b.metric("当前可售价格计划", len(available))
    metric_c.metric("本轮变动 ≥ ¥10", len(changed))

    if not available.empty:
        summary = (
            available.groupby("hotel_name", as_index=False)["price_yuan"]
            .min()
            .sort_values("price_yuan")
        )
        figure = px.bar(
            summary,
            x="price_yuan",
            y="hotel_name",
            orientation="h",
            text="price_yuan",
            labels={"price_yuan": "当前最低可售价（元/晚）", "hotel_name": "酒店"},
            title="各酒店当前最低可售房型价",
        )
        figure.update_traces(texttemplate="¥%{text:.0f}", textposition="outside")
        figure.update_layout(height=max(280, 65 * len(summary)), margin=dict(l=10, r=30, t=55, b=10))
        st.plotly_chart(figure, use_container_width=True)

    st.subheader("房型价格明细")
    st.dataframe(
        display_table(latest),
        use_container_width=True,
        hide_index=True,
        column_config={
            "当前价格（元/晚）": st.column_config.NumberColumn(format="¥%.0f"),
            "上一轮价格（元/晚）": st.column_config.NumberColumn(format="¥%.0f"),
            "变动（元）": st.column_config.NumberColumn(format="%+.0f"),
        },
        height=520,
    )
    if not sold_or_review.empty:
        st.caption(f"另有 {len(sold_or_review)} 条售罄、异常或待人工核验记录，详见“采集状态”。")

elif page == "价格历史":
    st.title("价格历史")
    st.caption("同一酒店、房型和价格计划随采集时间的实际价格变化。")
    hotel = st.selectbox("查看酒店", sorted(filtered["hotel_name"].unique()))
    history = filtered[(filtered["hotel_name"] == hotel) & (filtered["price_yuan"].notna())].copy()
    plans = history["room_name"].fillna("未识别房型").unique().tolist()
    selected_plans = st.multiselect("房型", sorted(plans), default=sorted(plans))
    history["房型"] = history["room_name"].fillna("未识别房型")
    history = history[history["房型"].isin(selected_plans)].sort_values("fetched_at")
    if history.empty:
        st.info("没有可绘制的价格历史。")
    else:
        figure = px.line(
            history,
            x="fetched_at",
            y="price_yuan",
            color="房型",
            symbol="rate_plan_key",
            markers=True,
            labels={"fetched_at": "采集时间", "price_yuan": "价格（元/晚）", "rate_plan_key": "价格计划"},
        )
        figure.update_layout(height=480, hovermode="x unified", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(figure, use_container_width=True)
    st.subheader("历史记录")
    st.dataframe(display_table(history.sort_values("fetched_at", ascending=False)), use_container_width=True, hide_index=True, height=420)

else:
    st.title("采集状态")
    st.caption("用于确认售罄、价格未展示、解析异常和待人工核验的结果。")
    statuses = sorted(latest["状态"].dropna().unique())
    selected_statuses = st.multiselect("状态", statuses, default=statuses)
    status_view = latest[latest["状态"].isin(selected_statuses)].copy()
    counts = status_view["状态"].value_counts().rename_axis("状态").reset_index(name="记录数")
    st.bar_chart(counts.set_index("状态"))
    st.dataframe(display_table(status_view), use_container_width=True, hide_index=True, height=520)
