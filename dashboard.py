"""
Vietravel Analytics Dashboard — version 2.0 (aggregate-based, scale-ready).

Dashboard đọc từ `data/aggregates/*.parquet` (pre-computed offline bởi etl_aggregates.py)
thay vì toàn bộ raw CSV. Với 7M events, aggregates chỉ ~200MB còn raw có thể >1GB.
Session Explorer drill-down dùng DuckDB query trực tiếp Parquet (local hoặc HF Hub URL).

Chạy:  streamlit run dashboard.py
ENV:
  RAW_PARQUET_URL   URL Parquet raw trên HF Hub (optional, default = data/events_clean.parquet local)
"""

import json
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).parent
AGG = BASE / "data" / "aggregates"
META_PATH = AGG / "meta.json"
RAW_PARQUET = os.environ.get(
    "RAW_PARQUET_URL",
    str(BASE / "data" / "events_clean.parquet"),
)

FUNNEL_EVENTS = ["Page View", "Search Tour", "Add To Cart",
                 "Order Booking", "Booking Success"]
FUNNEL_COL_MAP = {
    "Page View": "reached_Page_View",
    "Search Tour": "reached_Search_Tour",
    "Add To Cart": "reached_Add_To_Cart",
    "Order Booking": "reached_Order_Booking",
    "Booking Success": "reached_Booking_Success",
}


# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="Vietravel Analytics",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .main-header {
    background: linear-gradient(90deg, #0066cc 0%, #00a8e8 100%);
    padding: 1.2rem 1.5rem; border-radius: 10px; color: white !important;
    margin-bottom: 1rem;
  }
  .main-header h1, .main-header p { color: white !important; margin: 0; }
  .main-header h1 { font-size: 1.6rem; }
  .main-header p  { opacity: 0.9; font-size: 0.9rem; }
  [data-testid="stMetric"] {
    background: #ffffff; padding: 14px 18px; border-radius: 10px;
    border: 1px solid #e3e5e8; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }
  [data-testid="stMetric"] * { color: #1f2937 !important; }
  [data-testid="stMetricLabel"] p { color: #5f6368 !important; font-weight: 500; }
  [data-testid="stMetricValue"] {
    font-size: 1.6rem !important; font-weight: 700; color: #0f172a !important;
  }
  [data-testid="stMetricDelta"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ==================== LOADERS (cached) ====================
@st.cache_data(show_spinner=False)
def load_meta() -> dict:
    return json.loads(META_PATH.read_text())


@st.cache_data(show_spinner="Loading aggregates…")
def load_agg(name: str) -> pd.DataFrame:
    return pd.read_parquet(AGG / name)


@st.cache_resource
def get_duckdb():
    con = duckdb.connect()
    # Setup httpfs cho remote Parquet nếu cần
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


meta = load_meta()
sessions = load_agg("sessions.parquet")
patterns = load_agg("patterns.parquet")
session_pattern = load_agg("session_pattern.parquet")
funnel_rollup = load_agg("funnel_rollup.parquet")
hourly = load_agg("hourly.parquet")
destinations = load_agg("destinations.parquet")
utm_sources = load_agg("utm_sources.parquet")
device_agg = load_agg("device.parquet")
tour_conv_all = load_agg("tour_conversion.parquet")
event_duration = load_agg("event_duration.parquet")
top_tours_all = load_agg("top_tours.parquet")


# ==================== HEADER ====================
st.markdown("""
<div class="main-header">
  <h1>✈️ Vietravel Analytics Dashboard</h1>
  <p>Customer journey &amp; tour conversion insights — aggregate-based</p>
</div>
""", unsafe_allow_html=True)


# ==================== SIDEBAR FILTERS ====================
st.sidebar.header("🔎 Bộ lọc")

device_options = ["(Tất cả)"] + meta["devices"]
device_filter = st.sidebar.selectbox("Thiết bị", device_options)

utm_options = ["(Tất cả)"] + meta["utm_sources"]
utm_filter = st.sidebar.selectbox("UTM source", utm_options)

country_options = ["(Tất cả)"] + meta["countries"]
country_filter = st.sidebar.selectbox("Quốc gia", country_options)

exclude_home = st.sidebar.checkbox("Loại homepage khỏi duration", value=True)

date_min = pd.to_datetime(meta["date_min"]).date() if meta["date_min"] else None
date_max = pd.to_datetime(meta["date_max"]).date() if meta["date_max"] else None
if date_min and date_max:
    date_range = st.sidebar.date_input(
        "Khoảng ngày (local VN)",
        value=(date_min, date_max),
        min_value=date_min, max_value=date_max,
    )
else:
    date_range = None


# --- Apply filter ONCE on sessions, use downstream ---
def filter_sessions(sess: pd.DataFrame) -> pd.DataFrame:
    mask = pd.Series(True, index=sess.index)
    if device_filter != "(Tất cả)":
        mask &= sess["device_type"] == device_filter
    if utm_filter != "(Tất cả)":
        mask &= sess["utm_source"] == utm_filter
    if country_filter != "(Tất cả)":
        mask &= sess["geo_country"] == country_filter
    if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
        d0, d1 = date_range
        dc = sess["date_local"]
        mask &= dc.notna() & (dc >= d0) & (dc <= d1)
    return sess[mask]


sess_f = filter_sessions(sessions)

st.sidebar.markdown("---")
st.sidebar.metric("Sessions (sau filter)", f"{len(sess_f):,}")
st.sidebar.metric("Tổng events (raw)", f"{meta['total_events']:,}")
st.sidebar.caption(
    f"Data size on disk: ~{sum(f.stat().st_size for f in AGG.glob('*.parquet')) / 1024:.0f} KB aggregates")


# ==================== KPI TOP ====================
c1, c2, c3, c4, c5, c6 = st.columns(6)
n_sessions = len(sess_f)
n_users = sess_f["profile_id"].nunique()
n_events_all = meta["total_events"]
atc_sess = int(sess_f["had_add_to_cart"].sum())
order_sess = int(sess_f["had_order"].sum())
success_sess = int(sess_f["had_success"].sum())
atc_rate = atc_sess / n_sessions * 100 if n_sessions else 0
order_rate = order_sess / n_sessions * 100 if n_sessions else 0

c1.metric("👥 Sessions", f"{n_sessions:,}")
c2.metric("🙋 Unique users", f"{n_users:,}")
c3.metric("📊 Total events", f"{n_events_all:,}")
c4.metric("🛒 Add To Cart", f"{atc_sess}", f"{atc_rate:.2f}%")
c5.metric("📝 Order Booking", f"{order_sess}", f"{order_rate:.2f}%")
c6.metric("✅ Booking Success", f"{success_sess}")


# ==================== TABS ====================
tab_funnel, tab_journey, tab_tour, tab_dest, tab_device, tab_time = st.tabs(
    ["🎯 Funnel", "🧭 Customer Journey", "💼 Tour Conversion",
     "🌏 Destinations & UTM", "📱 Device", "⏰ Time patterns"]
)


# -------------------- Tab 1: Funnel --------------------
with tab_funnel:
    st.subheader("Sales Funnel")
    rows = []
    prev = None
    for ev in FUNNEL_EVENTS:
        col = mapping = {
            "Page View": "had_page_view",
            "Search Tour": "had_search",
            "Add To Cart": "had_add_to_cart",
            "Order Booking": "had_order",
            "Booking Success": "had_success",
        }[ev]
        s = int(sess_f[col].sum())
        drop = (prev - s) / prev * 100 if prev else 0
        rows.append({"step": ev, "sessions": s, "drop_pct": round(drop, 1)})
        prev = s if s else prev
    funnel_df = pd.DataFrame(rows)

    cf1, cf2 = st.columns([2, 1])
    with cf1:
        fig = go.Figure(go.Funnel(
            y=funnel_df["step"], x=funnel_df["sessions"],
            textposition="inside", textinfo="value+percent previous",
            marker={"color": ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd"]},
        ))
        fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cf2:
        st.dataframe(funnel_df, hide_index=True, use_container_width=True)
        st.info("**Quan sát:** Drop lớn nhất thường ở bước Search Tour → Add To Cart. "
                "Đây là điểm cần tối ưu UX ưu tiên.")

    # Funnel theo device
    st.markdown("### Funnel theo thiết bị")
    split_rows = []
    for dev in sess_f["device_type"].dropna().unique():
        sub = sess_f[sess_f["device_type"] == dev]
        total = len(sub)
        for ev in FUNNEL_EVENTS:
            col = {
                "Page View": "had_page_view", "Search Tour": "had_search",
                "Add To Cart": "had_add_to_cart", "Order Booking": "had_order",
                "Booking Success": "had_success",
            }[ev]
            s = int(sub[col].sum())
            split_rows.append({"device": dev, "step": ev, "sessions": s,
                               "rate_pct": round(s / total * 100, 2) if total else 0})
    split_df = pd.DataFrame(split_rows)
    fig = px.bar(split_df, x="step", y="rate_pct", color="device", barmode="group",
                 text="rate_pct", category_orders={"step": FUNNEL_EVENTS},
                 labels={"rate_pct": "% sessions"})
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


# -------------------- Tab 2: Customer Journey --------------------
with tab_journey:
    st.subheader("Top event patterns (đường đi phổ biến)")

    # Join sessions (sau filter) với session_pattern để lấy pattern của các session khớp filter
    filtered_sids = set(sess_f["session_id"])
    sp_f = session_pattern[session_pattern["session_id"].isin(filtered_sids)]
    pat_counts = sp_f["pattern"].value_counts().reset_index()
    pat_counts.columns = ["pattern", "sessions"]
    pat_counts["share_pct"] = (pat_counts["sessions"] / len(sess_f) * 100
                               if len(sess_f) else 0).round(2)
    pat_counts.insert(0, "rank", range(1, len(pat_counts) + 1))

    kw = st.text_input(
        "🔍 Tìm pattern chứa từ khoá (vd: 'Booking Success', 'Add To Cart')",
        value="",
    )
    if kw.strip():
        pat_view = pat_counts[
            pat_counts["pattern"].str.contains(kw.strip(), case=False, na=False)
        ]
        total_kw = int(pat_view["sessions"].sum())
        st.caption(f"Có **{len(pat_view)}** pattern khớp, tổng **{total_kw}** sessions.")
    else:
        pat_view = pat_counts

    cj1, cj2 = st.columns([3, 2])
    with cj1:
        top_chart = pat_view.head(15).sort_values("sessions")
        fig = px.bar(top_chart, x="sessions", y="pattern", orientation="h",
                     text="share_pct")
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(height=500, margin=dict(l=0, r=0, t=10, b=10),
                          yaxis_title="", xaxis_title="sessions")
        st.plotly_chart(fig, use_container_width=True)
    with cj2:
        show = pat_view if kw.strip() else pat_view.head(50)
        st.dataframe(show, hide_index=True, use_container_width=True, height=500)

    # Thời gian từng event
    st.markdown("### ⏱️ Thời gian trung bình ở từng loại event")
    st.caption("Duration tính bằng hiệu thời điểm event với event kế tiếp trong "
               "cùng session, lọc 0 < d ≤ 30 phút. Event cuối session không đo được.")
    ct1, ct2 = st.columns([3, 2])
    with ct1:
        fig = px.bar(event_duration.sort_values("avg_sec"),
                     x="avg_sec", y="name", orientation="h",
                     text="avg_sec",
                     hover_data=["events_measured", "median_sec", "p90_sec", "coverage_pct"],
                     labels={"avg_sec": "Avg duration (giây)", "name": ""},
                     color="avg_sec", color_continuous_scale="Blues")
        fig.update_traces(texttemplate="%{text:.1f}s", textposition="outside")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10),
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    with ct2:
        st.dataframe(event_duration, hide_index=True, use_container_width=True, height=360)

    # Session Explorer
    st.markdown("---")
    st.subheader("🔍 Session explorer")
    cjf1, cjf2, cjf3 = st.columns(3)
    min_events = cjf1.number_input("Min events / session", 1, 50, 3)
    only_conv = cjf2.checkbox("Chỉ session có ATC / Order", False)
    only_success = cjf3.checkbox("Chỉ session có Booking Success", False)

    cond = sess_f["num_events"] >= min_events
    if only_conv:
        cond &= sess_f["had_add_to_cart"] | sess_f["had_order"]
    if only_success:
        cond &= sess_f["had_success"]
    jshow = sess_f[cond].sort_values("num_events", ascending=False).head(200)

    st.dataframe(
        jshow[["session_id", "profile_id", "num_events", "num_unique_links",
               "session_duration_min", "utm_source", "device_type",
               "had_add_to_cart", "had_order", "had_success"]],
        hide_index=True, use_container_width=True, height=300,
    )

    pick = st.selectbox(
        "Chọn session để xem timeline chi tiết:",
        options=["(không chọn)"] + jshow["session_id"].tolist(),
    )
    if pick != "(không chọn)":
        # DuckDB query raw parquet cho 1 session cụ thể
        con = get_duckdb()
        raw_q = f"""
            SELECT create, name, type,
                   context_page_url, context_page_title, context_page_ld_name
            FROM read_parquet('{RAW_PARQUET}')
            WHERE session_id = ?
            ORDER BY create
        """
        with st.spinner("Querying raw events …"):
            try:
                ses = con.execute(raw_q, [pick]).fetchdf()
            except Exception as e:
                st.error(f"Lỗi query raw parquet: {e}")
                ses = pd.DataFrame()

        if len(ses) == 0:
            st.warning("Không tìm thấy events cho session này.")
        else:
            ses["create_ts"] = pd.to_datetime(ses["create"], utc=True).dt.tz_localize(None)
            ses = ses.sort_values("create_ts").reset_index(drop=True)
            ses["next_ts"] = ses["create_ts"].shift(-1)
            ses["duration_sec"] = (ses["next_ts"] - ses["create_ts"]).dt.total_seconds()

            total_min = (ses["create_ts"].max() - ses["create_ts"].min()).total_seconds() / 60
            total_measured = ses["duration_sec"].dropna().clip(upper=1800).sum()

            k1, k2, k3 = st.columns(3)
            k1.metric("Events", len(ses))
            k2.metric("Tổng thời gian session", f"{total_min:.1f} phút")
            k3.metric("Tổng time đo được", f"{total_measured:.0f} giây")

            def fmt(v):
                if pd.isna(v):
                    return "— (event cuối / không đo được)"
                if v > 1800:
                    return f"{v:.0f}s (> 30p, outlier)"
                return f"{v:.1f}s"

            show = ses[["create_ts", "name", "type",
                        "context_page_url", "context_page_ld_name"]].copy()
            show.insert(0, "step", range(1, len(show) + 1))
            show["duration"] = ses["duration_sec"].map(fmt)
            show.columns = ["step", "time", "event", "type", "url", "tour_name", "duration"]
            st.dataframe(show, hide_index=True, use_container_width=True, height=400)

            # Step duration chart
            st.markdown("**Thời gian ở từng step:**")
            step_df = ses.copy()
            step_df["step"] = [f"{i+1}. {n}" for i, n in enumerate(step_df["name"].astype(str))]
            step_df["duration_plot"] = step_df["duration_sec"].where(
                (step_df["duration_sec"] > 0) & (step_df["duration_sec"] <= 1800), other=0)
            fig = px.bar(step_df, x="step", y="duration_plot",
                         text="duration_plot", color="name",
                         labels={"duration_plot": "Duration (giây)", "step": ""})
            fig.update_traces(texttemplate="%{text:.0f}s", textposition="outside")
            fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10),
                              xaxis_tickangle=-30, legend_title="Event type")
            st.plotly_chart(fig, use_container_width=True)


# -------------------- Tab 3: Tour Conversion --------------------
with tab_tour:
    st.subheader("Tỷ lệ chốt tour theo số views & thời gian")

    # Filter tour_conv dùng same filter như sidebar (joined via profile_attrs ở ETL)
    tc = tour_conv_all.copy()
    if device_filter != "(Tất cả)":
        tc = tc[tc["device_type"] == device_filter]
    if utm_filter != "(Tất cả)":
        tc = tc[tc["utm_source"] == utm_filter]
    if country_filter != "(Tất cả)":
        tc = tc[tc["geo_country"] == country_filter]

    def bucket_views(v):
        if v == 1: return "1"
        if v == 2: return "2"
        if v <= 4: return "3-4"
        if v <= 9: return "5-9"
        return "10+"

    def bucket_time(t):
        if pd.isna(t) or t == 0: return "0 (no duration)"
        if t < 30: return "<30s"
        if t < 60: return "30-60s"
        if t < 180: return "1-3min"
        if t < 600: return "3-10min"
        return "10min+"

    tc["views_bucket"] = tc["views"].map(bucket_views)
    tc["time_bucket"] = tc["total_time"].map(bucket_time)

    order = ["1", "2", "3-4", "5-9", "10+"]
    torder = ["0 (no duration)", "<30s", "30-60s", "1-3min", "3-10min", "10min+"]

    ct1, ct2 = st.columns(2)

    with ct1:
        st.markdown("#### Intent rate theo số VIEWS")
        by_v = (tc.groupby("views_bucket")
                .agg(users=("profile_id", "count"),
                     intent_pct=("intent", lambda s: round(s.mean() * 100, 2)),
                     booked_pct=("booked", lambda s: round(s.mean() * 100, 2)))
                .reset_index())
        by_v["views_bucket"] = pd.Categorical(by_v["views_bucket"], categories=order, ordered=True)
        by_v = by_v.sort_values("views_bucket")
        fig = px.bar(by_v, x="views_bucket", y="intent_pct", text="intent_pct",
                     hover_data=["users", "booked_pct"],
                     labels={"intent_pct": "Intent rate (%)", "views_bucket": "Số views"})
        fig.update_traces(texttemplate="%{text}%", textposition="outside",
                          marker_color="#1f77b4")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(by_v, hide_index=True, use_container_width=True)

    with ct2:
        st.markdown("#### Intent rate theo THỜI GIAN ở trang")
        by_t = (tc.groupby("time_bucket")
                .agg(users=("profile_id", "count"),
                     intent_pct=("intent", lambda s: round(s.mean() * 100, 2)),
                     booked_pct=("booked", lambda s: round(s.mean() * 100, 2)))
                .reset_index())
        by_t["time_bucket"] = pd.Categorical(by_t["time_bucket"], categories=torder, ordered=True)
        by_t = by_t.sort_values("time_bucket")
        fig = px.bar(by_t, x="time_bucket", y="intent_pct", text="intent_pct",
                     hover_data=["users", "booked_pct"],
                     labels={"intent_pct": "Intent rate (%)", "time_bucket": "Tổng thời gian"})
        fig.update_traces(texttemplate="%{text}%", textposition="outside",
                          marker_color="#2ca02c")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(by_t, hide_index=True, use_container_width=True)

    # Heatmap
    st.markdown("#### Heatmap: Views × Time → Intent rate")
    pivot = (tc.groupby(["views_bucket", "time_bucket"])
             .agg(intent_pct=("intent", lambda s: s.mean() * 100),
                  users=("profile_id", "count"))
             .reset_index())
    p_intent = pivot.pivot(index="views_bucket", columns="time_bucket", values="intent_pct")
    p_users = pivot.pivot(index="views_bucket", columns="time_bucket", values="users")

    nonempty_rows = [r for r in order if r in p_intent.index]
    nonempty_cols = [c for c in torder if c in p_intent.columns]
    p_intent = p_intent.reindex(index=nonempty_rows[::-1], columns=nonempty_cols)
    p_users = p_users.reindex(index=nonempty_rows[::-1], columns=nonempty_cols)

    text_matrix = []
    for i in range(len(p_intent.index)):
        row = []
        for j in range(len(p_intent.columns)):
            val = p_intent.iloc[i, j]; n = p_users.iloc[i, j]
            if pd.isna(val):
                row.append("<i>—</i>")
            else:
                n_str = f"{int(n)}" if not pd.isna(n) else "0"
                row.append(f"<b>{val:.1f}%</b><br><span style='font-size:10px'>n={n_str}</span>")
        text_matrix.append(row)

    fig = go.Figure(go.Heatmap(
        z=p_intent.values, x=p_intent.columns.tolist(), y=p_intent.index.tolist(),
        text=text_matrix, texttemplate="%{text}", textfont=dict(size=13),
        colorscale="Blues", colorbar=dict(title="Intent %", ticksuffix="%"),
        hovertemplate="Views: <b>%{y}</b><br>Time: <b>%{x}</b><br>"
                      "Intent rate: %{z:.2f}%<extra></extra>",
        xgap=2, ygap=2, zmin=0,
    ))
    fig.update_layout(
        height=max(200, 80 * len(p_intent.index) + 100),
        margin=dict(l=60, r=20, t=20, b=60),
        xaxis=dict(title="Tổng thời gian ở trang", type="category",
                   categoryorder="array", categoryarray=nonempty_cols),
        yaxis=dict(title="Số lần xem tour", type="category",
                   categoryorder="array", categoryarray=nonempty_rows[::-1]),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
    missing_rows = [r for r in order if r not in nonempty_rows]
    note = "Ô trống (—) = không có user thuộc bucket đó."
    if missing_rows:
        note += (f" Bucket **{', '.join(missing_rows)}** đã ẩn vì không có user "
                 f"nào xem tour nhiều đến mức đó.")
    st.caption(note)

    # Correlation
    st.markdown("#### Correlation (views/time → intent)")
    corr_rows = []
    y = tc["intent"].astype(int)
    for col in ["views", "total_time", "avg_time"]:
        x = tc[col].fillna(0)
        corr_rows.append({
            "metric": col,
            "pearson_r": round(x.corr(y, method="pearson"), 4),
            "spearman_r": round(x.corr(y, method="spearman"), 4),
        })
    st.dataframe(pd.DataFrame(corr_rows), hide_index=True, use_container_width=True)

    # Top tours (pre-computed, không phụ thuộc filter)
    st.markdown("#### Top 20 tour thu hút nhất (toàn bộ data, không filter)")
    st.dataframe(top_tours_all.head(20), hide_index=True, use_container_width=True, height=360)


# -------------------- Tab 4: Destinations & UTM --------------------
with tab_dest:
    cd1, cd2 = st.columns(2)

    with cd1:
        st.subheader("🌏 Top điểm đến")
        # Filter destinations bởi device/utm từ sidebar
        dest_f = destinations.copy()
        if device_filter != "(Tất cả)":
            dest_f = dest_f[dest_f["device_type"] == device_filter]
        if utm_filter != "(Tất cả)":
            dest_f = dest_f[dest_f["utm_source"] == utm_filter]
        dest_agg = (dest_f.groupby("destination")
                    .agg(views=("views", "sum"),
                         sessions=("sessions", "sum"),
                         users=("users", "sum"))
                    .reset_index()
                    .sort_values("views", ascending=False)
                    .head(15))
        fig = px.bar(dest_agg.sort_values("views"), x="views", y="destination",
                     orientation="h", text="views",
                     hover_data=["sessions", "users"])
        fig.update_layout(height=500, margin=dict(l=0, r=0, t=10, b=10),
                          yaxis_title="")
        fig.update_traces(marker_color="#ff7f0e", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    with cd2:
        st.subheader("📣 UTM Source performance")
        utm_f = utm_sources.copy()
        if device_filter != "(Tất cả)":
            utm_f = utm_f[utm_f["device_type"] == device_filter]

        # Aggregate over devices, kept by utm_source
        utm_agg = (utm_f.groupby("utm_source", dropna=False)
                   .agg(sessions=("sessions", "sum"),
                        atc=("atc_count", "sum"),
                        order=("order_count", "sum"))
                   .reset_index())
        utm_agg["atc_pct"] = (utm_agg["atc"] / utm_agg["sessions"] * 100).round(2)
        utm_agg["order_pct"] = (utm_agg["order"] / utm_agg["sessions"] * 100).round(2)
        utm_agg = utm_agg.sort_values("sessions", ascending=False).head(10)
        utm_agg["utm_source"] = utm_agg["utm_source"].fillna("(direct / không UTM)")
        utm_agg["label"] = (utm_agg["utm_source"] + " ("
                            + utm_agg["sessions"].map("{:,}".format) + ")")

        utm_sorted = utm_agg.sort_values("atc_pct", ascending=True)
        fig = go.Figure()
        fig.add_bar(y=utm_sorted["label"], x=utm_sorted["atc_pct"],
                    name="🛒 Add To Cart %", orientation="h",
                    marker_color="#1f77b4",
                    text=utm_sorted["atc_pct"].map("{:.2f}%".format),
                    textposition="outside")
        fig.add_bar(y=utm_sorted["label"], x=utm_sorted["order_pct"],
                    name="📝 Order Booking %", orientation="h",
                    marker_color="#ff7f0e",
                    text=utm_sorted["order_pct"].map("{:.2f}%".format),
                    textposition="outside")
        fig.update_layout(barmode="group", height=500, margin=dict(l=0, r=40, t=10, b=10),
                          xaxis_title="Conversion rate (%)", yaxis_title="",
                          legend=dict(orientation="h", y=1.08, x=0),
                          plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "🛒 **ATC rate** = % sessions có Add To Cart  •  "
            "📝 **Order rate** = % sessions có Order Booking  •  "
            "**(direct)** = không gắn UTM (gõ thẳng URL / bookmark / organic).")
        st.dataframe(utm_agg[["utm_source", "sessions", "atc_pct", "order_pct"]],
                     hide_index=True, use_container_width=True)


# -------------------- Tab 5: Device --------------------
with tab_device:
    st.subheader("📱 Device analysis")

    dev = (device_agg.groupby("device_type", dropna=False)
           .agg(sessions=("sessions", "sum"),
                atc=("atc_count", "sum"),
                order=("order_count", "sum"),
                avg_events=("avg_events", "mean"),
                avg_duration_min=("avg_duration_min", "mean"))
           .reset_index())
    dev["share_pct"] = (dev["sessions"] / dev["sessions"].sum() * 100).round(2)
    dev["atc_rate_pct"] = (dev["atc"] / dev["sessions"] * 100).round(3)
    dev["order_rate_pct"] = (dev["order"] / dev["sessions"] * 100).round(3)
    dev["avg_events"] = dev["avg_events"].round(2)
    dev["avg_duration_min"] = dev["avg_duration_min"].round(2)
    dev = dev.sort_values("sessions", ascending=False)

    cdv1, cdv2 = st.columns([1, 1])
    with cdv1:
        fig = px.pie(dev, values="sessions", names="device_type", hole=0.5,
                     title="Tỷ lệ sessions theo thiết bị")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cdv2:
        fig = px.bar(dev, x="device_type", y=["atc_rate_pct", "order_rate_pct"],
                     barmode="group", title="Conversion rate theo device",
                     text_auto=".2f")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=40, b=10),
                          yaxis_title="%", legend_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        dev[["device_type", "sessions", "share_pct",
             "atc_rate_pct", "order_rate_pct",
             "avg_events", "avg_duration_min"]],
        hide_index=True, use_container_width=True,
    )

    st.markdown("#### OS breakdown")
    os_stats = (device_agg.groupby("os_name", dropna=False)
                .agg(events=("sessions", "sum"), sessions=("sessions", "sum"))
                .reset_index()
                .sort_values("sessions", ascending=False).head(10))
    os_stats["os_name"] = os_stats["os_name"].fillna("Unknown")
    fig = px.bar(os_stats.sort_values("sessions"), x="sessions", y="os_name",
                 orientation="h", text="sessions")
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=10), yaxis_title="")
    fig.update_traces(marker_color="#9467bd", textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


# -------------------- Tab 6: Time --------------------
with tab_time:
    st.subheader("⏰ Hoạt động theo thời gian")

    # Filter hourly by date range
    hour_df = hourly.copy()
    if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
        d0, d1 = date_range
        hour_df = hour_df[(hour_df["date_local"] >= d0) & (hour_df["date_local"] <= d1)]

    # Aggregate by hour
    hour_agg = (hour_df.groupby("hour_local")
                .agg(page_views=("page_views", "sum"),
                     intent_events=("intent_events", "sum"))
                .reset_index())
    hour_agg["intent_rate_pct"] = (
        hour_agg["intent_events"] / hour_agg["page_views"].replace(0, np.nan) * 100
    ).round(2)

    view_mode = st.radio(
        "Cách hiển thị:",
        ["Dual axis (số tuyệt đối)", "Intent rate % / giờ (1 trục duy nhất)"],
        horizontal=True,
    )

    if view_mode.startswith("Dual"):
        fig = go.Figure()
        fig.add_bar(
            x=hour_agg["hour_local"], y=hour_agg["page_views"],
            name="Page Views (trục trái)", marker_color="#1f77b4", yaxis="y",
            customdata=np.stack([hour_agg["intent_events"],
                                 hour_agg["intent_rate_pct"].fillna(0)], axis=-1),
            hovertemplate="<b>%{x}h</b><br>Page Views: <b>%{y:,}</b><br>"
                          "Intent events: %{customdata[0]:,}<br>"
                          "Intent rate: %{customdata[1]:.2f}%<extra></extra>",
        )
        fig.add_scatter(
            x=hour_agg["hour_local"], y=hour_agg["intent_events"],
            name="Intent events (trục phải)", mode="lines+markers",
            line=dict(color="#d62728", width=3), marker=dict(size=9), yaxis="y2",
            hovertemplate="Intent: <b>%{y}</b><extra></extra>",
        )
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=40, b=10), hovermode="x unified",
            xaxis=dict(title="Giờ (GMT+7)", dtick=1),
            yaxis=dict(title=dict(text="📊 Page Views  (trục trái)",
                                  font=dict(color="#1f77b4", size=13)),
                       tickfont=dict(color="#1f77b4"),
                       gridcolor="rgba(31,119,180,0.12)"),
            yaxis2=dict(title=dict(text="🎯 Intent events  (trục phải)",
                                   font=dict(color="#d62728", size=13)),
                        tickfont=dict(color="#d62728"),
                        overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("⚠️ Dual-axis: bar (xanh) đo theo trục TRÁI, đường (đỏ) đo theo trục PHẢI. "
                   "Hover để thấy cả 2 giá trị cùng lúc.")
    else:
        fig = go.Figure()
        fig.add_bar(
            x=hour_agg["hour_local"], y=hour_agg["intent_rate_pct"],
            marker_color="#d62728",
            text=hour_agg["intent_rate_pct"].map(
                lambda v: f"{v:.1f}%" if pd.notna(v) else ""),
            textposition="outside",
            customdata=np.stack([hour_agg["page_views"],
                                 hour_agg["intent_events"]], axis=-1),
            hovertemplate="<b>%{x}h</b><br>Intent rate: <b>%{y:.2f}%</b><br>"
                          "Page Views: %{customdata[0]:,}<br>"
                          "Intent events: %{customdata[1]:,}<extra></extra>",
        )
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=20, b=10),
            xaxis=dict(title="Giờ (GMT+7)", dtick=1),
            yaxis=dict(title="Intent rate (%)", ticksuffix="%"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Intent rate % = Intent events / Page Views × 100. "
                   "Giờ có tỷ lệ cao = 'decision hour'.")

    # Daily trend — build từ sessions agg + funnel_rollup
    st.markdown("#### Xu hướng theo ngày")
    scale_mode = st.radio("Thang đo:",
                          ["Log (thấy được cả event hiếm)", "Linear (so sánh tỉ lệ thực)"],
                          horizontal=True, key="daily_scale")

    # Rebuild daily counts từ funnel_rollup
    fr = funnel_rollup.copy()
    # Apply same filter
    if device_filter != "(Tất cả)":
        fr = fr[fr["device_type"] == device_filter]
    if utm_filter != "(Tất cả)":
        fr = fr[fr["utm_source"] == utm_filter]
    if country_filter != "(Tất cả)":
        fr = fr[fr["geo_country"] == country_filter]
    if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
        d0, d1 = date_range
        fr = fr[(fr["date_local"] >= d0) & (fr["date_local"] <= d1)]

    daily_rows = []
    for ev, col in FUNNEL_COL_MAP.items():
        s = fr.groupby("date_local")[col].sum().reset_index()
        s.columns = ["date_local", "count"]
        s["name"] = ev
        daily_rows.append(s)
    daily = pd.concat(daily_rows, ignore_index=True)
    daily = daily[daily["count"] > 0]

    color_map = {
        "Page View": "#1f77b4", "Search Tour": "#17becf",
        "Add To Cart": "#ff7f0e", "Order Booking": "#d62728",
        "Booking Success": "#2ca02c",
    }
    fig = px.line(daily, x="date_local", y="count", color="name", markers=True,
                  color_discrete_map=color_map,
                  category_orders={"name": FUNNEL_EVENTS},
                  hover_data={"count": ":,"})
    fig.update_traces(line=dict(width=2.5), marker=dict(size=8))

    if scale_mode.startswith("Log"):
        tick_vals = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
        fmt = lambda v: f"{v/1000:g}k" if v >= 1000 else str(v)
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=10, b=10),
            yaxis=dict(type="log", title="Số events (log scale)",
                       tickmode="array", tickvals=tick_vals,
                       ticktext=[fmt(v) for v in tick_vals]),
            xaxis_title="", hovermode="x unified",
        )
    else:
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=10, b=10),
            yaxis=dict(title="Số events", tickformat=","),
            xaxis_title="", hovermode="x unified",
        )
    st.plotly_chart(fig, use_container_width=True)
    if scale_mode.startswith("Log"):
        st.caption("ℹ️ **Log scale**: mỗi khoảng trục Y = nhân 10. "
                   "Tick '20' là hai mươi, không phải hai.")


# ==================== FOOTER ====================
st.markdown("---")
st.caption(
    f"Data: {meta['total_events']:,} events  •  {meta['total_sessions']:,} sessions  •  "
    f"Aggregates total: {sum(f.stat().st_size for f in AGG.glob('*.parquet')) / 1024:.0f} KB  •  "
    f"Built with Streamlit + Plotly + DuckDB"
)
