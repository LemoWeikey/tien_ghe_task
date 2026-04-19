"""
Phân tích hành vi khách hàng từ full_data_clean.csv:

  1) Customer Journey — đường đi của từng session (events + links).
  2) Funnel — tỷ lệ chuyển giữa các bước.
  3) Tour conversion — tỷ lệ chốt tour theo số lượt view & thời gian xem.
  4) Thống kê du lịch có ý nghĩa — điểm đến, attribution, device, thời gian.

Output: nhiều file CSV + 1 file Excel tổng hợp có nhiều sheet.
"""

import re
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path("/Users/jamesgatsby/tien_ghe_task")
SRC = BASE / "full_data_clean.csv"
OUT_XLSX = BASE / "analysis_report.xlsx"

# ==================== LOAD & CHUẨN HOÁ ====================
df = pd.read_csv(SRC, low_memory=False)
df["create_ts"] = pd.to_datetime(df["create"], utc=True, errors="coerce")
df["tour_pid"] = df["context_page_url"].astype(str).str.extract(r"-pid-(\d+)")[0]

# link đã có sẵn trong file clean, nhưng đảm bảo strip fragment
def canon(u):
    if not isinstance(u, str):
        return None
    return u.split("#")[0]


df["link"] = df["context_page_url"].map(canon)

# Loại homepage khỏi phân tích duration
HOMEPAGE = {"https://travel.com.vn/", "https://travel.com.vn",
            "http://travel.com.vn/", "http://travel.com.vn"}

# Rebuild duration trong cùng session (được cả fullset, không bị lọc sẵn)
df = df.sort_values(["session_id", "create_ts"]).reset_index(drop=True)
df["next_ts"] = df.groupby("session_id")["create_ts"].shift(-1)
df["duration_sec"] = (df["next_ts"] - df["create_ts"]).dt.total_seconds()

# Tour code từ properties (dùng cho Add To Cart / Order Booking)
# Tour code dạng "NNSGN358-008-240126DR-H-6-F" -> trùng URL pid qua lookup sản phẩm
# Ta dùng tour_pid làm khoá hợp nhất (đã có ở cả 2 phía).

CONVERSION_EVENTS = {"Add To Cart", "Order Booking", "Booking Success"}
INTENT_EVENTS = {"Add To Cart", "Order Booking"}   # có tour_pid
BOOKING_COMPLETED = {"Order Booking", "Booking Success"}

writer = pd.ExcelWriter(OUT_XLSX, engine="xlsxwriter")


# ==================== 1) CUSTOMER JOURNEY ====================
# Gộp theo session_id → sequence events và links
g = df.groupby("session_id")
journeys = pd.DataFrame({
    "profile_id": g["profile_id"].first(),
    "started_at": g["create_ts"].min(),
    "ended_at": g["create_ts"].max(),
    "num_events": g.size(),
    "num_unique_links": g["link"].nunique(),
    "event_sequence": g["name"].apply(lambda s: " > ".join(s.astype(str).tolist())),
    "link_sequence": g["link"].apply(
        lambda s: " > ".join([u for u in s.dropna().astype(str).tolist()])
    ),
    "utm_source": g["utm_source"].first(),
    "utm_medium": g["utm_medium"].first(),
    "utm_campaign": g["utm_campaign"].first(),
    "device_type": g["device_type"].first(),
    "geo_country": g["device_geo_country_code"].first(),
    "geo_city": g["device_geo_city"].first(),
    "had_search": g["name"].apply(lambda s: (s == "Search Tour").any()),
    "had_add_to_cart": g["name"].apply(lambda s: (s == "Add To Cart").any()),
    "had_order_booking": g["name"].apply(lambda s: (s == "Order Booking").any()),
    "had_booking_success": g["name"].apply(lambda s: (s == "Booking Success").any()),
}).reset_index()
journeys["session_duration_min"] = (
    (journeys["ended_at"] - journeys["started_at"]).dt.total_seconds() / 60
).round(2)
# strip tz cho Excel
for c in ["started_at", "ended_at"]:
    journeys[c] = journeys[c].dt.tz_localize(None)

journeys.to_excel(writer, sheet_name="1_journey_per_session", index=False)

# Pattern phổ biến nhất (rút gọn event sequence bỏ lặp liên tiếp)
def compress(seq):
    parts = seq.split(" > ")
    out = [parts[0]]
    for p in parts[1:]:
        if p != out[-1]:
            out.append(p)
    return " > ".join(out)


journeys["event_pattern"] = journeys["event_sequence"].map(compress)
top_patterns = (
    journeys["event_pattern"].value_counts().head(30).reset_index()
)
top_patterns.columns = ["event_pattern", "sessions"]
top_patterns["share_%"] = (top_patterns["sessions"] / len(journeys) * 100).round(2)
top_patterns.to_excel(writer, sheet_name="2_top_event_patterns", index=False)


# ==================== 2) FUNNEL ====================
funnel_events = ["Page View", "Search Tour", "Add To Cart",
                 "Order Booking", "Booking Success"]
funnel_rows = []
prev = None
for ev in funnel_events:
    sessions_reaching = df[df["name"] == ev]["session_id"].nunique()
    row = {
        "step": ev,
        "sessions_reaching": sessions_reaching,
        "share_of_all_sessions_%": round(sessions_reaching / df["session_id"].nunique() * 100, 2),
        "drop_from_previous_%": (
            round((prev - sessions_reaching) / prev * 100, 2) if prev else np.nan
        ),
    }
    funnel_rows.append(row)
    prev = sessions_reaching
funnel = pd.DataFrame(funnel_rows)
funnel.to_excel(writer, sheet_name="3_funnel", index=False)


# ==================== 3) TOUR CONVERSION theo views & duration ====================
# Gom hành vi theo (profile_id, tour_pid).
# - views = số Page View trên tour detail của tour đó
# - total_time = tổng duration_sec của các Page View đó (sau lọc outlier)
# - converted = profile này có Add To Cart / Order Booking trên tour đó?

tour_views = df[
    (df["name"] == "Page View")
    & (df["tour_pid"].notna())
    & (~df["link"].isin(HOMEPAGE))
].copy()
# Dùng duration có sẵn; lọc outlier 0 < d ≤ 30 min
tour_views["valid_duration"] = tour_views["duration_sec"].where(
    (tour_views["duration_sec"] > 0) & (tour_views["duration_sec"] <= 1800)
)

view_agg = (
    tour_views.groupby(["profile_id", "tour_pid"])
    .agg(
        views=("id", "count"),
        total_time_sec=("valid_duration", "sum"),
        avg_time_sec=("valid_duration", "mean"),
        last_visit=("create_ts", "max"),
        tour_name=("context_page_ld_name", "first"),
    )
    .reset_index()
)

# Conversion set: (profile_id, tour_pid) có Intent / Completed
intent_pairs = set(
    zip(
        df.loc[df["name"].isin(INTENT_EVENTS), "profile_id"],
        df.loc[df["name"].isin(INTENT_EVENTS), "tour_pid"],
    )
)
booking_pairs = set(
    zip(
        df.loc[df["name"] == "Order Booking", "profile_id"],
        df.loc[df["name"] == "Order Booking", "tour_pid"],
    )
)

view_agg["intent"] = [
    (p, t) in intent_pairs for p, t in zip(view_agg["profile_id"], view_agg["tour_pid"])
]
view_agg["booked"] = [
    (p, t) in booking_pairs for p, t in zip(view_agg["profile_id"], view_agg["tour_pid"])
]
view_agg["last_visit"] = pd.to_datetime(view_agg["last_visit"], utc=True).dt.tz_localize(None)

view_agg.to_excel(writer, sheet_name="4_tour_view_per_user", index=False)

# --- Bucket theo số views ---
def bucket_views(v):
    if v == 1:
        return "1"
    if v == 2:
        return "2"
    if v <= 4:
        return "3-4"
    if v <= 9:
        return "5-9"
    return "10+"


view_agg["views_bucket"] = view_agg["views"].map(bucket_views)

by_views = (
    view_agg.groupby("views_bucket")
    .agg(
        users=("profile_id", "count"),
        intent_rate_pct=("intent", lambda s: round(s.mean() * 100, 2)),
        booked_rate_pct=("booked", lambda s: round(s.mean() * 100, 2)),
    )
    .reset_index()
)
# sort tự nhiên
order = ["1", "2", "3-4", "5-9", "10+"]
by_views["views_bucket"] = pd.Categorical(by_views["views_bucket"], categories=order, ordered=True)
by_views = by_views.sort_values("views_bucket")
by_views.to_excel(writer, sheet_name="5_conv_by_views", index=False)

# --- Bucket theo tổng thời gian (giây) ---
def bucket_time(t):
    if pd.isna(t) or t == 0:
        return "0 (no duration)"
    if t < 30:
        return "<30s"
    if t < 60:
        return "30-60s"
    if t < 180:
        return "1-3min"
    if t < 600:
        return "3-10min"
    return "10min+"


view_agg["time_bucket"] = view_agg["total_time_sec"].map(bucket_time)
time_order = ["0 (no duration)", "<30s", "30-60s", "1-3min", "3-10min", "10min+"]

by_time = (
    view_agg.groupby("time_bucket")
    .agg(
        users=("profile_id", "count"),
        intent_rate_pct=("intent", lambda s: round(s.mean() * 100, 2)),
        booked_rate_pct=("booked", lambda s: round(s.mean() * 100, 2)),
    )
    .reset_index()
)
by_time["time_bucket"] = pd.Categorical(by_time["time_bucket"], categories=time_order, ordered=True)
by_time = by_time.sort_values("time_bucket")
by_time.to_excel(writer, sheet_name="6_conv_by_time", index=False)

# --- Cross: views × time ---
cross = (
    view_agg.groupby(["views_bucket", "time_bucket"])
    .agg(
        users=("profile_id", "count"),
        intent_rate_pct=("intent", lambda s: round(s.mean() * 100, 2) if len(s) else 0),
    )
    .reset_index()
)
cross_pivot = cross.pivot(index="views_bucket", columns="time_bucket",
                          values="intent_rate_pct")
cross_pivot.to_excel(writer, sheet_name="7_conv_views_x_time")

# --- Correlation ---
corr_rows = []
# Dùng intent (201 events) làm label vì mẫu Booking quá nhỏ (34)
y = view_agg["intent"].astype(int)
for col in ["views", "total_time_sec", "avg_time_sec"]:
    x = view_agg[col].fillna(0)
    pearson = x.corr(y, method="pearson")
    spearman = x.corr(y, method="spearman")
    corr_rows.append({"metric": col,
                      "pearson_r": round(pearson, 4),
                      "spearman_r": round(spearman, 4)})
pd.DataFrame(corr_rows).to_excel(writer, sheet_name="8_correlation", index=False)


# ==================== 4) THỐNG KÊ DU LỊCH ====================
# A) Điểm đến nổi bật — từ URL /du-lich-<destination>.aspx
df["destination"] = df["context_page_url"].astype(str).str.extract(
    r"/du-lich-([a-zA-Z0-9\-]+)\.aspx"
)[0]
dest_stats = (
    df[df["destination"].notna() & (df["name"] == "Page View")]
    .groupby("destination")
    .agg(
        views=("id", "count"),
        unique_sessions=("session_id", "nunique"),
        unique_users=("profile_id", "nunique"),
    )
    .reset_index()
    .sort_values("views", ascending=False)
    .head(30)
)
dest_stats.to_excel(writer, sheet_name="9_top_destinations", index=False)

# B) UTM source → conversion
utm_stats = (
    journeys.groupby("utm_source", dropna=False)
    .agg(
        sessions=("session_id", "count"),
        atc_rate_pct=("had_add_to_cart",
                      lambda s: round(s.mean() * 100, 2)),
        order_rate_pct=("had_order_booking",
                        lambda s: round(s.mean() * 100, 2)),
        avg_events=("num_events", "mean"),
    )
    .reset_index()
    .sort_values("sessions", ascending=False)
)
utm_stats["avg_events"] = utm_stats["avg_events"].round(2)
utm_stats.to_excel(writer, sheet_name="10_utm_source_conv", index=False)

# C) Device / OS
device_stats = (
    journeys.groupby("device_type", dropna=False)
    .agg(
        sessions=("session_id", "count"),
        share_pct=("session_id", lambda s: round(len(s) / len(journeys) * 100, 2)),
        atc_rate_pct=("had_add_to_cart", lambda s: round(s.mean() * 100, 2)),
        order_rate_pct=("had_order_booking", lambda s: round(s.mean() * 100, 2)),
    )
    .reset_index()
    .sort_values("sessions", ascending=False)
)
device_stats.to_excel(writer, sheet_name="11_device", index=False)

# D) Giờ trong ngày (theo UTC, trừ 7h cho VN cũng OK, nhưng giữ UTC cho đơn giản)
df["hour_local"] = (df["create_ts"] + pd.Timedelta(hours=7)).dt.hour
hour_stats = (
    df[df["name"] == "Page View"].groupby("hour_local").size()
    .reset_index(name="pageviews")
)
# thêm booking theo giờ
booking_hour = (
    df[df["name"].isin(INTENT_EVENTS)].groupby("hour_local").size()
    .reset_index(name="intent_events")
)
hour_stats = hour_stats.merge(booking_hour, on="hour_local", how="left").fillna(0)
hour_stats.to_excel(writer, sheet_name="12_hour_of_day", index=False)

# E) Geo — country / city
geo_stats = (
    df.groupby(["device_geo_country_code", "device_geo_city"])
    .agg(
        sessions=("session_id", "nunique"),
        events=("id", "count"),
    )
    .reset_index()
    .sort_values("sessions", ascending=False)
    .head(30)
)
geo_stats.to_excel(writer, sheet_name="13_geo", index=False)


# ==================== TỔNG KẾT ====================
summary_rows = [
    ["Total events", len(df)],
    ["Unique sessions", df["session_id"].nunique()],
    ["Unique profiles", df["profile_id"].nunique()],
    ["Sessions có search", int(journeys["had_search"].sum())],
    ["Sessions có Add To Cart", int(journeys["had_add_to_cart"].sum())],
    ["Sessions có Order Booking", int(journeys["had_order_booking"].sum())],
    ["Sessions có Booking Success", int(journeys["had_booking_success"].sum())],
    ["Conversion rate ATC (% session)",
     round(journeys["had_add_to_cart"].mean() * 100, 3)],
    ["Conversion rate Order (% session)",
     round(journeys["had_order_booking"].mean() * 100, 3)],
    ["Avg events / session", round(journeys["num_events"].mean(), 2)],
    ["Median events / session", int(journeys["num_events"].median())],
    ["Avg session duration (min)",
     round(journeys["session_duration_min"].mean(), 2)],
    ["Unique (user, tour) pairs với ≥1 view",
     len(view_agg)],
    ["… trong đó có intent (ATC/Order)", int(view_agg["intent"].sum())],
    ["… trong đó có Order Booking", int(view_agg["booked"].sum())],
    ["Overall intent rate %", round(view_agg["intent"].mean() * 100, 3)],
    ["Overall booking rate %", round(view_agg["booked"].mean() * 100, 3)],
]
pd.DataFrame(summary_rows, columns=["metric", "value"]).to_excel(
    writer, sheet_name="0_summary", index=False
)

writer.close()
print(f"Đã ghi báo cáo: {OUT_XLSX.name}")

# In ra màn hình những kết quả chính
print("\n===== FUNNEL =====")
print(funnel.to_string(index=False))
print("\n===== TOP EVENT PATTERNS (sau khi collapse duplicate) =====")
print(top_patterns.head(15).to_string(index=False))
print("\n===== CONVERSION theo số VIEWS (intent = Add To Cart hoặc Order Booking) =====")
print(by_views.to_string(index=False))
print("\n===== CONVERSION theo TOTAL TIME (intent) =====")
print(by_time.to_string(index=False))
print("\n===== CORRELATION (views/time → intent) =====")
print(pd.DataFrame(corr_rows).to_string(index=False))
print("\n===== TOP 10 DESTINATIONS =====")
print(dest_stats.head(10).to_string(index=False))
print("\n===== UTM SOURCE (top 10) =====")
print(utm_stats.head(10).to_string(index=False))
print("\n===== DEVICE =====")
print(device_stats.to_string(index=False))
