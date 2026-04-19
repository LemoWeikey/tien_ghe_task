"""
Vietravel Analytics Dashboard
Chạy:  streamlit run dashboard.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).parent
SRC = BASE / "full_data_clean.csv"

HOMEPAGE = {
    "https://travel.com.vn/", "https://travel.com.vn",
    "http://travel.com.vn/", "http://travel.com.vn",
}
INTENT_EVENTS = {"Add To Cart", "Order Booking"}
FUNNEL_EVENTS = ["Page View", "Search Tour", "Add To Cart",
                 "Order Booking", "Booking Success"]

# ==================== Page config ====================
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
    padding: 1.2rem 1.5rem;
    border-radius: 10px;
    color: white !important;
    margin-bottom: 1rem;
  }
  .main-header h1, .main-header p { color: white !important; margin: 0; }
  .main-header h1 { font-size: 1.6rem; }
  .main-header p  { opacity: 0.9; font-size: 0.9rem; }

  /* KPI metric cards — ép chữ tối trên nền trắng cho cả light & dark mode */
  [data-testid="stMetric"] {
    background: #ffffff;
    padding: 14px 18px;
    border-radius: 10px;
    border: 1px solid #e3e5e8;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }
  [data-testid="stMetric"] * { color: #1f2937 !important; }
  [data-testid="stMetricLabel"] p {
    color: #5f6368 !important;
    font-weight: 500;
  }
  [data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 700;
    color: #0f172a !important;
  }
  /* Delta (phần % tăng/giảm) */
  [data-testid="stMetricDelta"] {
    font-weight: 600;
  }
  [data-testid="stMetricDelta"] svg { fill: currentColor !important; }
</style>
""", unsafe_allow_html=True)


# ==================== Data loading ====================
@st.cache_data(show_spinner="Loading data…")
def load_data():
    df = pd.read_csv(SRC, low_memory=False)
    df["create_ts"] = pd.to_datetime(df["create"], utc=True, errors="coerce")
    df["tour_pid"] = df["context_page_url"].astype(str).str.extract(r"-pid-(\d+)")[0]

    def canon(u):
        if not isinstance(u, str):
            return None
        return u.split("#")[0]

    df["link"] = df["context_page_url"].map(canon)
    df["destination"] = df["context_page_url"].astype(str).str.extract(
        r"/du-lich-([a-zA-Z0-9\-]+)\.aspx")[0]
    df = df.sort_values(["session_id", "create_ts"]).reset_index(drop=True)
    df["next_ts"] = df.groupby("session_id")["create_ts"].shift(-1)
    df["duration_sec"] = (df["next_ts"] - df["create_ts"]).dt.total_seconds()
    df["hour_local"] = (df["create_ts"] + pd.Timedelta(hours=7)).dt.hour
    df["date_local"] = (df["create_ts"] + pd.Timedelta(hours=7)).dt.date
    return df


@st.cache_data(show_spinner="Building journeys…")
def build_journeys(df):
    g = df.groupby("session_id")
    j = pd.DataFrame({
        "profile_id": g["profile_id"].first(),
        "started_at": g["create_ts"].min(),
        "ended_at": g["create_ts"].max(),
        "num_events": g.size(),
        "num_unique_links": g["link"].nunique(),
        "utm_source": g["utm_source"].first(),
        "utm_medium": g["utm_medium"].first(),
        "device_type": g["device_type"].first(),
        "geo_country": g["device_geo_country_code"].first(),
        "geo_city": g["device_geo_city"].first(),
        "had_search": g["name"].apply(lambda s: (s == "Search Tour").any()),
        "had_add_to_cart": g["name"].apply(lambda s: (s == "Add To Cart").any()),
        "had_order": g["name"].apply(lambda s: (s == "Order Booking").any()),
        "had_success": g["name"].apply(lambda s: (s == "Booking Success").any()),
    }).reset_index()
    j["session_duration_min"] = (
        (j["ended_at"] - j["started_at"]).dt.total_seconds() / 60
    ).round(2)
    return j


df = load_data()
journeys = build_journeys(df)


# ==================== Header ====================
st.markdown("""
<div class="main-header">
  <h1>✈️ Vietravel Analytics Dashboard</h1>
  <p>Customer journey &amp; tour conversion insights</p>
</div>
""", unsafe_allow_html=True)


# ==================== Sidebar filters ====================
st.sidebar.header("🔎 Bộ lọc")

device_options = ["(Tất cả)"] + sorted(df["device_type"].dropna().unique().tolist())
device_filter = st.sidebar.selectbox("Thiết bị", device_options)

utm_options = ["(Tất cả)"] + sorted(df["utm_source"].dropna().unique().tolist())
utm_filter = st.sidebar.selectbox("UTM source", utm_options)

country_options = ["(Tất cả)"] + sorted(
    df["device_geo_country_code"].dropna().unique().tolist()
)
country_filter = st.sidebar.selectbox("Quốc gia", country_options)

exclude_home = st.sidebar.checkbox("Loại homepage khỏi duration", value=True)

valid_dates = df["date_local"].dropna()
min_d, max_d = valid_dates.min(), valid_dates.max()
date_range = st.sidebar.date_input(
    "Khoảng ngày (local VN)", value=(min_d, max_d), min_value=min_d, max_value=max_d
)

# Apply filters
mask = pd.Series(True, index=df.index)
if device_filter != "(Tất cả)":
    mask &= df["device_type"] == device_filter
if utm_filter != "(Tất cả)":
    mask &= df["utm_source"] == utm_filter
if country_filter != "(Tất cả)":
    mask &= df["device_geo_country_code"] == country_filter
if isinstance(date_range, tuple) and len(date_range) == 2:
    d0, d1 = date_range
    date_col = df["date_local"]
    mask &= date_col.notna() & (date_col >= d0) & (date_col <= d1)

df_f = df[mask].copy()
sel_sessions = df_f["session_id"].unique()
j_f = journeys[journeys["session_id"].isin(sel_sessions)].copy()

st.sidebar.markdown("---")
st.sidebar.metric("Events (sau filter)", f"{len(df_f):,}")
st.sidebar.metric("Sessions (sau filter)", f"{len(j_f):,}")


# ==================== KPIs ====================
c1, c2, c3, c4, c5, c6 = st.columns(6)

n_sessions = len(j_f)
n_users = j_f["profile_id"].nunique()
n_events = len(df_f)
atc_sessions = int(j_f["had_add_to_cart"].sum())
order_sessions = int(j_f["had_order"].sum())
success_sessions = int(j_f["had_success"].sum())
atc_rate = atc_sessions / n_sessions * 100 if n_sessions else 0
order_rate = order_sessions / n_sessions * 100 if n_sessions else 0

c1.metric("👥 Sessions", f"{n_sessions:,}")
c2.metric("🙋 Unique users", f"{n_users:,}")
c3.metric("📊 Events", f"{n_events:,}")
c4.metric("🛒 Add To Cart", f"{atc_sessions}", f"{atc_rate:.2f}%")
c5.metric("📝 Order Booking", f"{order_sessions}", f"{order_rate:.2f}%")
c6.metric("✅ Booking Success", f"{success_sessions}")


# ==================== Tabs ====================
tab_funnel, tab_journey, tab_tour, tab_dest, tab_device, tab_time = st.tabs(
    ["🎯 Funnel", "🧭 Customer Journey", "💼 Tour Conversion",
     "🌏 Destinations & UTM", "📱 Device", "⏰ Time patterns"]
)

# ==================== Tab 1: Funnel ====================
with tab_funnel:
    st.subheader("Sales Funnel")
    funnel_rows = []
    prev = None
    for ev in FUNNEL_EVENTS:
        s = df_f[df_f["name"] == ev]["session_id"].nunique()
        drop = (prev - s) / prev * 100 if prev else 0
        funnel_rows.append({"step": ev, "sessions": s, "drop_%": round(drop, 1)})
        prev = s if s else prev
    funnel = pd.DataFrame(funnel_rows)

    cf1, cf2 = st.columns([2, 1])
    with cf1:
        fig = go.Figure(go.Funnel(
            y=funnel["step"],
            x=funnel["sessions"],
            textposition="inside",
            textinfo="value+percent previous",
            marker={"color": ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd"]},
        ))
        fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cf2:
        st.dataframe(funnel, hide_index=True, use_container_width=True)
        st.info(
            "**Quan sát:** Drop lớn nhất ở bước Search Tour → Add To Cart. "
            "Đây là điểm cần tối ưu UX ưu tiên."
        )

    # Funnel split by device
    st.markdown("### Funnel theo thiết bị")
    split_rows = []
    for dev in j_f["device_type"].dropna().unique():
        sub = df_f[df_f["device_type"] == dev]
        total = sub["session_id"].nunique()
        for ev in FUNNEL_EVENTS:
            s = sub[sub["name"] == ev]["session_id"].nunique()
            split_rows.append({
                "device": dev, "step": ev, "sessions": s,
                "rate_%": round(s / total * 100, 2) if total else 0,
            })
    split_df = pd.DataFrame(split_rows)
    fig = px.bar(
        split_df, x="step", y="rate_%", color="device", barmode="group",
        text="rate_%",
        category_orders={"step": FUNNEL_EVENTS},
        labels={"rate_%": "% sessions"},
    )
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


# ==================== Tab 2: Journey ====================
with tab_journey:
    st.subheader("Top event patterns (đường đi phổ biến)")

    def compress(seq):
        parts = seq.split(" > ")
        out = [parts[0]]
        for p in parts[1:]:
            if p != out[-1]:
                out.append(p)
        return " > ".join(out)

    seq = (
        df_f.sort_values(["session_id", "create_ts"])
        .groupby("session_id")["name"]
        .apply(lambda s: " > ".join(s.astype(str).tolist()))
    )
    patterns = seq.map(compress).value_counts().reset_index()
    patterns.columns = ["pattern", "sessions"]
    patterns["share_%"] = (patterns["sessions"] / len(j_f) * 100).round(2)
    patterns.insert(0, "rank", range(1, len(patterns) + 1))

    # Ô search keyword: lọc pattern chứa từ khoá (ví dụ "Booking Success")
    kw = st.text_input(
        "🔍 Tìm pattern chứa từ khoá (vd: 'Booking Success', 'Add To Cart')",
        value="",
    )
    if kw.strip():
        patterns_view = patterns[
            patterns["pattern"].str.contains(kw.strip(), case=False, na=False)
        ]
        total_sess_kw = int(patterns_view["sessions"].sum())
        st.caption(
            f"Có **{len(patterns_view)}** pattern khớp keyword, "
            f"tổng **{total_sess_kw}** sessions."
        )
    else:
        patterns_view = patterns

    cj1, cj2 = st.columns([3, 2])
    with cj1:
        top_for_chart = patterns_view.head(15).sort_values("sessions")
        fig = px.bar(
            top_for_chart,
            x="sessions", y="pattern", orientation="h",
            text="share_%",
        )
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(height=500, margin=dict(l=0, r=0, t=10, b=10),
                          yaxis_title="", xaxis_title="sessions")
        st.plotly_chart(fig, use_container_width=True)
    with cj2:
        # Hiển thị toàn bộ pattern khớp (hoặc top 50 nếu không filter)
        show_df = patterns_view if kw.strip() else patterns_view.head(50)
        st.dataframe(show_df, hide_index=True, use_container_width=True,
                     height=500)

    # ----- Thời gian trung bình ở từng loại event -----
    st.markdown("### ⏱️ Thời gian trung bình ở từng loại event")
    st.caption(
        "Duration tính bằng hiệu thời điểm event hiện tại và event kế tiếp "
        "trong cùng session. Đã lọc 0 < duration ≤ 30 phút. "
        "Các event cuối cùng của session (thường là Booking Success, "
        "Order Booking) không có 'next event' nên không đo được thời gian ở lại."
    )

    # Xác định event context: sessions khớp keyword (nếu có) hoặc toàn bộ
    if kw.strip():
        matched_seq = seq.map(compress)
        matched_sessions = matched_seq[
            matched_seq.str.contains(kw.strip(), case=False, na=False)
        ].index.tolist()
        df_time = df_f[df_f["session_id"].isin(matched_sessions)].copy()
        st.caption(
            f"📌 Thống kê dưới đây tính trên {len(matched_sessions)} sessions "
            f"khớp keyword **'{kw}'**."
        )
    else:
        df_time = df_f.copy()

    valid_d = (df_time["duration_sec"] > 0) & (df_time["duration_sec"] <= 1800)
    ev_time = (
        df_time[valid_d]
        .groupby("name")
        .agg(
            events_measured=("id", "count"),
            avg_sec=("duration_sec", "mean"),
            median_sec=("duration_sec", "median"),
            p90_sec=("duration_sec", lambda s: s.quantile(0.9)),
            total_min=("duration_sec", lambda s: s.sum() / 60),
        )
        .reset_index()
    )
    # Thêm count tổng (kể cả event không đo được)
    total_count = df_time.groupby("name").size().reset_index(name="events_total")
    ev_time = ev_time.merge(total_count, on="name", how="right").fillna(0)
    ev_time["coverage_%"] = (
        ev_time["events_measured"] / ev_time["events_total"] * 100
    ).round(1)
    ev_time = ev_time.sort_values("events_total", ascending=False)
    for c in ["avg_sec", "median_sec", "p90_sec", "total_min"]:
        ev_time[c] = ev_time[c].round(2)
    ev_time["events_measured"] = ev_time["events_measured"].astype(int)
    ev_time["events_total"] = ev_time["events_total"].astype(int)

    ct1, ct2 = st.columns([3, 2])
    with ct1:
        fig = px.bar(
            ev_time.sort_values("avg_sec"),
            x="avg_sec", y="name", orientation="h",
            text="avg_sec",
            hover_data=["events_measured", "median_sec", "p90_sec",
                        "coverage_%"],
            labels={"avg_sec": "Avg duration (giây)", "name": ""},
            color="avg_sec", color_continuous_scale="Blues",
        )
        fig.update_traces(texttemplate="%{text:.1f}s",
                          textposition="outside")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10),
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    with ct2:
        st.dataframe(
            ev_time[["name", "events_total", "events_measured",
                     "coverage_%", "avg_sec", "median_sec",
                     "p90_sec", "total_min"]],
            hide_index=True, use_container_width=True, height=360,
        )

    st.markdown("---")
    st.subheader("🔍 Session explorer")
    cjf1, cjf2, cjf3 = st.columns(3)
    min_events = cjf1.number_input("Min events / session", 1, 50, 3)
    only_conv = cjf2.checkbox("Chỉ session có Add To Cart / Order", False)
    only_success = cjf3.checkbox("Chỉ session có Booking Success", False)

    cond = j_f["num_events"] >= min_events
    if only_conv:
        cond &= j_f["had_add_to_cart"] | j_f["had_order"]
    if only_success:
        cond &= j_f["had_success"]
    jshow = j_f[cond].sort_values("num_events", ascending=False).head(200)

    st.dataframe(
        jshow[["session_id", "profile_id", "num_events", "num_unique_links",
               "session_duration_min", "utm_source", "device_type",
               "had_add_to_cart", "had_order", "had_success"]],
        hide_index=True, use_container_width=True, height=300,
    )

    pick = st.selectbox(
        "Chọn session để xem chi tiết:",
        options=["(không chọn)"] + jshow["session_id"].tolist()
    )
    if pick != "(không chọn)":
        ses = df_f[df_f["session_id"] == pick].sort_values("create_ts").copy()
        total_min = (ses["create_ts"].max() - ses["create_ts"].min()
                     ).total_seconds() / 60
        # Tổng thời gian đo được (sum duration của các event có next)
        total_measured_sec = ses["duration_sec"].dropna().clip(upper=1800).sum()

        k1, k2, k3 = st.columns(3)
        k1.metric("Events", len(ses))
        k2.metric("Tổng thời gian session", f"{total_min:.1f} phút")
        k3.metric("Tổng time đo được", f"{total_measured_sec:.0f} giây")

        show = ses[["create_ts", "name", "type", "link",
                    "context_page_ld_name", "duration_sec"]].copy()
        show["create_ts"] = show["create_ts"].dt.tz_localize(None)
        # Format duration: hiển thị "—" nếu event cuối (NaN), "X.Xs" nếu có
        def fmt_dur(v):
            if pd.isna(v):
                return "— (event cuối / không đo được)"
            if v > 1800:
                return f"{v:.0f}s (> 30p, outlier)"
            return f"{v:.1f}s"
        show["duration"] = show["duration_sec"].map(fmt_dur)
        show = show.drop(columns=["duration_sec"])
        show.insert(0, "step", range(1, len(show) + 1))
        st.dataframe(show, hide_index=True, use_container_width=True, height=400)

        # Bar chart time trong từng step của session
        st.markdown("**Thời gian ở từng step:**")
        step_df = ses.reset_index(drop=True).copy()
        step_df["step"] = [f"{i+1}. {n}" for i, n in
                           enumerate(step_df["name"].astype(str))]
        step_df["duration_plot"] = step_df["duration_sec"].where(
            (step_df["duration_sec"] > 0) & (step_df["duration_sec"] <= 1800),
            other=0,
        )
        fig = px.bar(
            step_df, x="step", y="duration_plot",
            text="duration_plot",
            labels={"duration_plot": "Duration (giây)", "step": ""},
            color="name",
        )
        fig.update_traces(texttemplate="%{text:.0f}s", textposition="outside")
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=10),
                          xaxis_tickangle=-30,
                          legend_title="Event type")
        st.plotly_chart(fig, use_container_width=True)


# ==================== Tab 3: Tour Conversion ====================
with tab_tour:
    st.subheader("Tỷ lệ chốt tour theo số views & thời gian")

    # Tour views aggregated
    tv = df_f[
        (df_f["name"] == "Page View")
        & df_f["tour_pid"].notna()
    ].copy()
    if exclude_home:
        tv = tv[~tv["link"].isin(HOMEPAGE)]
    tv["valid_d"] = tv["duration_sec"].where(
        (tv["duration_sec"] > 0) & (tv["duration_sec"] <= 1800))

    agg = (
        tv.groupby(["profile_id", "tour_pid"])
        .agg(views=("id", "count"),
             total_time=("valid_d", "sum"),
             avg_time=("valid_d", "mean"),
             tour_name=("context_page_ld_name", "first"))
        .reset_index()
    )

    intent_pairs = set(zip(
        df_f.loc[df_f["name"].isin(INTENT_EVENTS), "profile_id"],
        df_f.loc[df_f["name"].isin(INTENT_EVENTS), "tour_pid"]))
    booking_pairs = set(zip(
        df_f.loc[df_f["name"] == "Order Booking", "profile_id"],
        df_f.loc[df_f["name"] == "Order Booking", "tour_pid"]))

    agg["intent"] = [(p, t) in intent_pairs
                     for p, t in zip(agg["profile_id"], agg["tour_pid"])]
    agg["booked"] = [(p, t) in booking_pairs
                     for p, t in zip(agg["profile_id"], agg["tour_pid"])]

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

    agg["views_bucket"] = agg["views"].map(bucket_views)
    agg["time_bucket"] = agg["total_time"].map(bucket_time)

    ct1, ct2 = st.columns(2)

    with ct1:
        st.markdown("#### Intent rate theo số VIEWS")
        by_v = (agg.groupby("views_bucket")
                .agg(users=("profile_id", "count"),
                     intent_pct=("intent", lambda s: round(s.mean() * 100, 2)),
                     booked_pct=("booked", lambda s: round(s.mean() * 100, 2)))
                .reset_index())
        order = ["1", "2", "3-4", "5-9", "10+"]
        by_v["views_bucket"] = pd.Categorical(by_v["views_bucket"],
                                              categories=order, ordered=True)
        by_v = by_v.sort_values("views_bucket")
        fig = px.bar(by_v, x="views_bucket", y="intent_pct",
                     text="intent_pct",
                     hover_data=["users", "booked_pct"],
                     labels={"intent_pct": "Intent rate (%)",
                             "views_bucket": "Số views"})
        fig.update_traces(texttemplate="%{text}%", textposition="outside",
                          marker_color="#1f77b4")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(by_v, hide_index=True, use_container_width=True)

    with ct2:
        st.markdown("#### Intent rate theo THỜI GIAN ở trang")
        by_t = (agg.groupby("time_bucket")
                .agg(users=("profile_id", "count"),
                     intent_pct=("intent", lambda s: round(s.mean() * 100, 2)),
                     booked_pct=("booked", lambda s: round(s.mean() * 100, 2)))
                .reset_index())
        torder = ["0 (no duration)", "<30s", "30-60s", "1-3min", "3-10min", "10min+"]
        by_t["time_bucket"] = pd.Categorical(by_t["time_bucket"],
                                             categories=torder, ordered=True)
        by_t = by_t.sort_values("time_bucket")
        fig = px.bar(by_t, x="time_bucket", y="intent_pct",
                     text="intent_pct",
                     hover_data=["users", "booked_pct"],
                     labels={"intent_pct": "Intent rate (%)",
                             "time_bucket": "Tổng thời gian"})
        fig.update_traces(texttemplate="%{text}%", textposition="outside",
                          marker_color="#2ca02c")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(by_t, hide_index=True, use_container_width=True)

    st.markdown("#### Heatmap: Views × Time → Intent rate")
    pivot = (agg.groupby(["views_bucket", "time_bucket"])
             .agg(intent_pct=("intent", lambda s: s.mean() * 100),
                  users=("profile_id", "count"))
             .reset_index())
    p_intent = pivot.pivot(index="views_bucket", columns="time_bucket",
                           values="intent_pct")
    p_users = pivot.pivot(index="views_bucket", columns="time_bucket",
                          values="users")

    # Chỉ giữ các bucket có thực sự data để heatmap không bị rỗng tênh
    nonempty_rows = [r for r in order if r in p_intent.index]
    nonempty_cols = [c for c in torder if c in p_intent.columns]
    # Reverse Y để "nhiều views" ở trên cùng
    p_intent = p_intent.reindex(index=nonempty_rows[::-1],
                                columns=nonempty_cols)
    p_users = p_users.reindex(index=nonempty_rows[::-1],
                              columns=nonempty_cols)

    # Build text cho từng ô
    text_matrix = []
    for i in range(len(p_intent.index)):
        row = []
        for j in range(len(p_intent.columns)):
            val = p_intent.iloc[i, j]
            n = p_users.iloc[i, j]
            if pd.isna(val):
                row.append("<i>—</i>")
            else:
                n_str = f"{int(n)}" if not pd.isna(n) else "0"
                row.append(f"<b>{val:.1f}%</b><br><span style='font-size:10px'>n={n_str}</span>")
        text_matrix.append(row)

    fig = go.Figure(go.Heatmap(
        z=p_intent.values,
        x=p_intent.columns.tolist(),
        y=p_intent.index.tolist(),
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=13),
        colorscale="Blues",
        colorbar=dict(title="Intent %", ticksuffix="%"),
        hovertemplate="Views: <b>%{y}</b><br>Time: <b>%{x}</b><br>"
                      "Intent rate: %{z:.2f}%<extra></extra>",
        xgap=2, ygap=2,  # khoảng cách giữa các ô cho đỡ dính
        zmin=0,
    ))
    fig.update_layout(
        height=max(200, 80 * len(p_intent.index) + 100),
        margin=dict(l=60, r=20, t=20, b=60),
        xaxis=dict(
            title="Tổng thời gian ở trang",
            type="category",
            categoryorder="array",
            categoryarray=nonempty_cols,
            side="bottom",
        ),
        yaxis=dict(
            title="Số lần xem tour",
            type="category",
            categoryorder="array",
            categoryarray=nonempty_rows[::-1],
        ),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
    missing_rows = [r for r in order if r not in nonempty_rows]
    note = "Ô trống (—) = không có user thuộc bucket đó."
    if missing_rows:
        note += (f" Bucket **{', '.join(missing_rows)}** đã ẩn vì không có "
                 f"user nào xem tour nhiều đến mức đó.")
    st.caption(note)

    st.markdown("#### Correlation (views/time → intent)")
    corr_data = []
    y = agg["intent"].astype(int)
    for col in ["views", "total_time", "avg_time"]:
        x = agg[col].fillna(0)
        corr_data.append({
            "metric": col,
            "pearson_r": round(x.corr(y, method="pearson"), 4),
            "spearman_r": round(x.corr(y, method="spearman"), 4),
        })
    st.dataframe(pd.DataFrame(corr_data), hide_index=True,
                 use_container_width=True)

    st.markdown("#### Top tour thu hút nhất (nhiều views nhất)")
    top_tour = (agg.groupby(["tour_pid", "tour_name"])
                .agg(total_views=("views", "sum"),
                     unique_users=("profile_id", "nunique"),
                     total_time=("total_time", "sum"),
                     intents=("intent", "sum"),
                     bookings=("booked", "sum"))
                .reset_index()
                .sort_values("total_views", ascending=False)
                .head(20))
    top_tour["conv_%"] = (top_tour["intents"] / top_tour["unique_users"] * 100
                          ).round(2)
    st.dataframe(top_tour, hide_index=True, use_container_width=True, height=360)


# ==================== Tab 4: Destinations & UTM ====================
with tab_dest:
    cd1, cd2 = st.columns(2)

    with cd1:
        st.subheader("🌏 Top điểm đến")
        dest = (df_f[df_f["destination"].notna() & (df_f["name"] == "Page View")]
                .groupby("destination")
                .agg(views=("id", "count"),
                     sessions=("session_id", "nunique"),
                     users=("profile_id", "nunique"))
                .reset_index()
                .sort_values("views", ascending=False)
                .head(15))
        fig = px.bar(dest.sort_values("views"), x="views", y="destination",
                     orientation="h", text="views",
                     hover_data=["sessions", "users"])
        fig.update_layout(height=500, margin=dict(l=0, r=0, t=10, b=10),
                          yaxis_title="")
        fig.update_traces(marker_color="#ff7f0e", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    with cd2:
        st.subheader("📣 UTM Source performance")
        utm = (j_f.groupby("utm_source", dropna=False)
               .agg(sessions=("session_id", "count"),
                    atc_pct=("had_add_to_cart",
                             lambda s: round(s.mean() * 100, 2)),
                    order_pct=("had_order",
                               lambda s: round(s.mean() * 100, 2)))
               .reset_index()
               .sort_values("sessions", ascending=False)
               .head(10))
        utm["utm_source"] = utm["utm_source"].fillna("(direct / không UTM)")
        # Tên kênh + số sessions để nhìn là biết độ lớn
        utm["label"] = (utm["utm_source"] + " ("
                        + utm["sessions"].map("{:,}".format) + ")")

        # Sort theo ATC rate để kênh tốt lên trên
        utm_sorted = utm.sort_values("atc_pct", ascending=True)

        fig = go.Figure()
        fig.add_bar(
            y=utm_sorted["label"], x=utm_sorted["atc_pct"],
            name="🛒 Add To Cart %",
            orientation="h",
            marker_color="#1f77b4",
            text=utm_sorted["atc_pct"].map("{:.2f}%".format),
            textposition="outside",
        )
        fig.add_bar(
            y=utm_sorted["label"], x=utm_sorted["order_pct"],
            name="📝 Order Booking %",
            orientation="h",
            marker_color="#ff7f0e",
            text=utm_sorted["order_pct"].map("{:.2f}%".format),
            textposition="outside",
        )
        fig.update_layout(
            barmode="group",
            height=500, margin=dict(l=0, r=40, t=10, b=10),
            xaxis_title="Conversion rate (%)",
            yaxis_title="",
            legend=dict(orientation="h", y=1.08, x=0),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "🛒 **ATC rate** = % sessions có Add To Cart  •  "
            "📝 **Order rate** = % sessions có Order Booking (sâu hơn trong funnel)  •  "
            "**(direct)** = không gắn UTM (gõ thẳng URL / bookmark / organic)."
        )
        st.dataframe(
            utm[["utm_source", "sessions", "atc_pct", "order_pct"]],
            hide_index=True, use_container_width=True,
        )


# ==================== Tab 5: Device ====================
with tab_device:
    st.subheader("📱 Device analysis")

    dev = (j_f.groupby("device_type", dropna=False)
           .agg(sessions=("session_id", "count"),
                atc_pct=("had_add_to_cart",
                         lambda s: round(s.mean() * 100, 2)),
                order_pct=("had_order",
                           lambda s: round(s.mean() * 100, 2)),
                avg_events=("num_events", "mean"),
                avg_duration_min=("session_duration_min", "mean"))
           .reset_index()
           .sort_values("sessions", ascending=False))
    dev["share_%"] = (dev["sessions"] / dev["sessions"].sum() * 100).round(2)
    dev["avg_events"] = dev["avg_events"].round(2)
    dev["avg_duration_min"] = dev["avg_duration_min"].round(2)

    cdv1, cdv2 = st.columns([1, 1])
    with cdv1:
        fig = px.pie(dev, values="sessions", names="device_type", hole=0.5,
                     title="Tỷ lệ sessions theo thiết bị")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with cdv2:
        fig = px.bar(dev, x="device_type", y=["atc_pct", "order_pct"],
                     barmode="group", title="Conversion rate theo device",
                     text_auto=".2f")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=40, b=10),
                          yaxis_title="%", legend_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(dev, hide_index=True, use_container_width=True)

    st.markdown("#### OS breakdown")
    os_stats = (df_f.groupby("os_name", dropna=False)
                .agg(events=("id", "count"),
                     sessions=("session_id", "nunique"))
                .reset_index()
                .sort_values("sessions", ascending=False).head(10))
    os_stats["os_name"] = os_stats["os_name"].fillna("Unknown")
    fig = px.bar(os_stats.sort_values("sessions"), x="sessions", y="os_name",
                 orientation="h", text="sessions")
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=10),
                      yaxis_title="")
    fig.update_traces(marker_color="#9467bd", textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


# ==================== Tab 6: Time ====================
with tab_time:
    st.subheader("⏰ Hoạt động theo thời gian")

    # Hour of day
    hv = (df_f[df_f["name"] == "Page View"]
          .groupby("hour_local").size().reset_index(name="page_views"))
    hi = (df_f[df_f["name"].isin(INTENT_EVENTS)]
          .groupby("hour_local").size().reset_index(name="intent_events"))
    hour_df = hv.merge(hi, on="hour_local", how="left").fillna(0)
    hour_df["intent_rate_pct"] = (
        hour_df["intent_events"] / hour_df["page_views"].replace(0, np.nan) * 100
    ).round(2)

    view_mode = st.radio(
        "Cách hiển thị:",
        ["Dual axis (số tuyệt đối)", "Intent rate % / giờ (1 trục duy nhất)"],
        horizontal=True,
    )

    if view_mode.startswith("Dual"):
        fig = go.Figure()
        fig.add_bar(
            x=hour_df["hour_local"], y=hour_df["page_views"],
            name="Page Views (trục trái)",
            marker_color="#1f77b4",
            yaxis="y",
            customdata=np.stack([hour_df["intent_events"],
                                 hour_df["intent_rate_pct"].fillna(0)], axis=-1),
            hovertemplate=(
                "<b>%{x}h</b><br>"
                "Page Views: <b>%{y:,}</b><br>"
                "Intent events: %{customdata[0]:,}<br>"
                "Intent rate: %{customdata[1]:.2f}%"
                "<extra></extra>"
            ),
        )
        fig.add_scatter(
            x=hour_df["hour_local"], y=hour_df["intent_events"],
            name="Intent events (trục phải)",
            mode="lines+markers",
            line=dict(color="#d62728", width=3),
            marker=dict(size=9),
            yaxis="y2",
            hovertemplate="Intent: <b>%{y}</b><extra></extra>",
        )
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=40, b=10),
            hovermode="x unified",
            xaxis=dict(title="Giờ (GMT+7)", dtick=1),
            yaxis=dict(
                title=dict(text="📊 Page Views  (trục trái)",
                           font=dict(color="#1f77b4", size=13)),
                tickfont=dict(color="#1f77b4"),
                gridcolor="rgba(31,119,180,0.12)",
            ),
            yaxis2=dict(
                title=dict(text="🎯 Intent events  (trục phải)",
                           font=dict(color="#d62728", size=13)),
                tickfont=dict(color="#d62728"),
                overlaying="y", side="right",
                showgrid=False,
            ),
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "⚠️ **Lưu ý dual-axis:** bar (xanh) đo theo trục TRÁI, "
            "đường (đỏ) đo theo trục PHẢI — 2 thang đo khác nhau, "
            "không so sánh chiều cao vật lý trực tiếp được. "
            "Hover để thấy cả 2 giá trị cùng lúc."
        )
    else:
        # Chế độ 1 trục: intent rate %
        fig = go.Figure()
        fig.add_bar(
            x=hour_df["hour_local"], y=hour_df["intent_rate_pct"],
            marker_color="#d62728",
            text=hour_df["intent_rate_pct"].map(
                lambda v: f"{v:.1f}%" if pd.notna(v) else ""),
            textposition="outside",
            customdata=np.stack([hour_df["page_views"],
                                 hour_df["intent_events"]], axis=-1),
            hovertemplate=(
                "<b>%{x}h</b><br>"
                "Intent rate: <b>%{y:.2f}%</b><br>"
                "Page Views: %{customdata[0]:,}<br>"
                "Intent events: %{customdata[1]:,}"
                "<extra></extra>"
            ),
        )
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=20, b=10),
            xaxis=dict(title="Giờ (GMT+7)", dtick=1),
            yaxis=dict(title="Intent rate (%)", ticksuffix="%"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Intent rate % = Intent events / Page Views × 100. "
            "Giờ nào có tỷ lệ cao = 'decision hour', khách quyết định nhanh."
        )

    # Daily trend
    st.markdown("#### Xu hướng theo ngày")
    scale_mode = st.radio(
        "Thang đo:",
        ["Log (thấy được cả event hiếm)", "Linear (so sánh tỉ lệ thực)"],
        horizontal=True, key="daily_scale",
    )
    daily = (df_f.groupby(["date_local", "name"]).size()
             .reset_index(name="count"))
    daily = daily[daily["name"].isin(FUNNEL_EVENTS)]

    color_map = {
        "Page View": "#1f77b4",
        "Search Tour": "#17becf",
        "Add To Cart": "#ff7f0e",
        "Order Booking": "#d62728",
        "Booking Success": "#2ca02c",
    }
    fig = px.line(daily, x="date_local", y="count", color="name",
                  markers=True,
                  color_discrete_map=color_map,
                  category_orders={"name": FUNNEL_EVENTS},
                  hover_data={"count": ":,"})
    fig.update_traces(line=dict(width=2.5), marker=dict(size=8))

    if scale_mode.startswith("Log"):
        # Log scale với tick đầy đủ (1, 2, 5, 10, 20, 50, 100...) + label rõ
        tick_vals = [1, 2, 5, 10, 20, 50, 100, 200, 500,
                     1000, 2000, 5000, 10000]
        def fmt(v):
            if v >= 1000:
                return f"{v/1000:g}k"
            return str(v)
        tick_text = [fmt(v) for v in tick_vals]
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=10, b=10),
            yaxis=dict(
                type="log",
                title="Số events (log scale)",
                tickmode="array",
                tickvals=tick_vals,
                ticktext=tick_text,
            ),
            xaxis_title="",
            hovermode="x unified",
        )
    else:
        fig.update_layout(
            height=430, margin=dict(l=0, r=0, t=10, b=10),
            yaxis=dict(title="Số events", tickformat=","),
            xaxis_title="",
            hovermode="x unified",
        )
    st.plotly_chart(fig, use_container_width=True)
    if scale_mode.startswith("Log"):
        st.caption(
            "ℹ️ **Log scale**: mỗi khoảng trên trục Y = nhân 10 lần. "
            "Giúp nhìn đồng thời cả series nghìn (Page View) lẫn series đơn vị "
            "(Booking Success). Tick '20' là 20 thật, không phải 2."
        )


st.markdown("---")
st.caption("Data source: full_data_clean.csv  •  "
           "Built with Streamlit + Plotly")
