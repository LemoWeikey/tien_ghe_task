"""
ETL offline: tính trước các aggregate cho dashboard.

Chạy 1 lần sau khi có `data/events_clean.parquet`:
    python etl_aggregates.py

Sinh ra các file trong `data/aggregates/`:
- meta.parquet              — distinct values của filter (utm_source, country, device, date_range)
- sessions.parquet          — 1 row / session_id với enriched fields (KHÔNG commit nếu > 50MB cho 7M data)
- patterns.parquet          — top 1000 event pattern + sessions count
- funnel_rollup.parquet     — funnel count theo (date × device × utm_source × country × step)
- hourly.parquet            — page_views + intent_events theo hour × date
- destinations.parquet      — top destinations
- utm_sources.parquet       — UTM source × device
- device.parquet            — device × os × journey
- tour_conversion.parquet   — per (profile_id, tour_pid) cho tour-view analysis
- event_duration.parquet    — avg/median/p90 duration theo event name
- top_tours.parquet         — top 200 tour hot nhất

Tất cả dùng pyarrow zstd compression.
"""

from pathlib import Path
import re
import pandas as pd
import numpy as np
import duckdb

BASE = Path(__file__).parent
DATA = BASE / "data"
AGG = DATA / "aggregates"
AGG.mkdir(parents=True, exist_ok=True)

SRC_PARQUET = DATA / "events_clean.parquet"
if not SRC_PARQUET.exists():
    raise SystemExit(
        "❌ Chưa có data/events_clean.parquet — chạy process_full.py trước."
    )

HOMEPAGE = {
    "https://travel.com.vn/", "https://travel.com.vn",
    "http://travel.com.vn/", "http://travel.com.vn",
}
FUNNEL_EVENTS = ["Page View", "Search Tour", "Add To Cart",
                 "Order Booking", "Booking Success"]
INTENT_EVENTS = {"Add To Cart", "Order Booking"}


def write(df: pd.DataFrame, name: str) -> None:
    path = AGG / name
    df.to_parquet(path, compression="zstd", index=False)
    print(f"  {name}  {len(df):,} rows  "
          f"{path.stat().st_size / 1024:.1f} KB")


# ==================== LOAD & ENRICH ====================
print("Loading events_clean.parquet ...")
df = pd.read_parquet(SRC_PARQUET)
print(f"  {len(df):,} events  ×  {df.shape[1]} cols")

df["create_ts"] = pd.to_datetime(df["create"], utc=True, errors="coerce")
df["tour_pid"] = df["context_page_url"].astype(str).str.extract(r"-pid-(\d+)")[0]
df["link"] = df["context_page_url"].astype(str).apply(
    lambda u: u.split("#")[0] if isinstance(u, str) else None)
df["destination"] = df["context_page_url"].astype(str).str.extract(
    r"/du-lich-([a-zA-Z0-9\-]+)\.aspx")[0]
df["hour_local"] = (df["create_ts"] + pd.Timedelta(hours=7)).dt.hour
df["date_local"] = (df["create_ts"] + pd.Timedelta(hours=7)).dt.date

# Recompute duration để chắc chắn consistent với dashboard logic
df = df.sort_values(["session_id", "create_ts"]).reset_index(drop=True)
df["next_ts"] = df.groupby("session_id")["create_ts"].shift(-1)
df["duration_sec"] = (df["next_ts"] - df["create_ts"]).dt.total_seconds()

print("\nWriting aggregates to data/aggregates/ ...")

# ==================== 1. META: filter options ====================
meta = {
    "utm_sources": sorted(df["utm_source"].dropna().unique().tolist()),
    "countries": sorted(df["device_geo_country_code"].dropna().unique().tolist()),
    "devices": sorted(df["device_type"].dropna().unique().tolist()),
    "date_min": str(df["date_local"].dropna().min()) if df["date_local"].notna().any() else None,
    "date_max": str(df["date_local"].dropna().max()) if df["date_local"].notna().any() else None,
    "total_events": int(len(df)),
    "total_sessions": int(df["session_id"].nunique()),
    "total_profiles": int(df["profile_id"].nunique()),
}
# Lưu dạng parquet (1 row, JSON-serialized) hoặc JSON
import json
(AGG / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
print(f"  meta.json  ({len(meta)} fields)")


# ==================== 2. SESSIONS (per-session rollup) ====================
g = df.groupby("session_id")
sessions = pd.DataFrame({
    "profile_id": g["profile_id"].first(),
    "started_at": g["create_ts"].min(),
    "ended_at": g["create_ts"].max(),
    "num_events": g.size(),
    "num_unique_links": g["link"].nunique(),
    "utm_source": g["utm_source"].first(),
    "utm_medium": g["utm_medium"].first(),
    "utm_campaign": g["utm_campaign"].first(),
    "device_type": g["device_type"].first(),
    "os_name": g["os_name"].first(),
    "geo_country": g["device_geo_country_code"].first(),
    "geo_city": g["device_geo_city"].first(),
    "had_page_view": g["name"].apply(lambda s: (s == "Page View").any()),
    "had_search": g["name"].apply(lambda s: (s == "Search Tour").any()),
    "had_add_to_cart": g["name"].apply(lambda s: (s == "Add To Cart").any()),
    "had_order": g["name"].apply(lambda s: (s == "Order Booking").any()),
    "had_success": g["name"].apply(lambda s: (s == "Booking Success").any()),
}).reset_index()
sessions["started_at"] = sessions["started_at"].dt.tz_localize(None)
sessions["ended_at"] = sessions["ended_at"].dt.tz_localize(None)
sessions["date_local"] = sessions["started_at"].dt.date
sessions["session_duration_min"] = (
    (sessions["ended_at"] - sessions["started_at"]).dt.total_seconds() / 60
).round(2)
write(sessions, "sessions.parquet")


# ==================== 3. PATTERNS (event sequences) ====================
def compress(seq: str) -> str:
    parts = seq.split(" > ")
    out = [parts[0]]
    for p in parts[1:]:
        if p != out[-1]:
            out.append(p)
    return " > ".join(out)

seq = (
    df.sort_values(["session_id", "create_ts"])
    .groupby("session_id")["name"]
    .apply(lambda s: " > ".join(s.astype(str).tolist()))
)
patterns_series = seq.map(compress)

# Map pattern -> list sessions (để giữ khả năng filter sau này)
pattern_counts = patterns_series.value_counts().reset_index()
pattern_counts.columns = ["pattern", "sessions"]
pattern_counts["share_pct"] = (
    pattern_counts["sessions"] / len(sessions) * 100).round(2)
pattern_counts.insert(0, "rank", range(1, len(pattern_counts) + 1))
# Chỉ giữ top 1000 để nhẹ
write(pattern_counts.head(1000), "patterns.parquet")

# Mapping session_id -> pattern (để drill-down nhanh)
sess_pattern = patterns_series.reset_index()
sess_pattern.columns = ["session_id", "pattern"]
write(sess_pattern, "session_pattern.parquet")


# ==================== 4. FUNNEL ROLLUP ====================
# Group sessions theo (date, device, utm_source, country), đếm bao nhiêu session đạt mỗi step.
sess_for_funnel = sessions.copy()
for step in FUNNEL_EVENTS:
    col = "had_" + step.lower().replace(" ", "_").replace("_view", "_view")
    # mapping: Page View -> had_page_view, Search Tour -> had_search, ...
    pass  # map below

mapping = {
    "Page View": "had_page_view",
    "Search Tour": "had_search",
    "Add To Cart": "had_add_to_cart",
    "Order Booking": "had_order",
    "Booking Success": "had_success",
}

rows = []
group_cols = ["date_local", "device_type", "utm_source", "geo_country"]
for keys, sub in sess_for_funnel.groupby(group_cols, dropna=False):
    date_local, dev, utm, country = keys
    row = {
        "date_local": date_local,
        "device_type": dev, "utm_source": utm, "geo_country": country,
        "sessions": len(sub),
    }
    for step, col in mapping.items():
        row[f"reached_{step.replace(' ', '_')}"] = int(sub[col].sum())
    rows.append(row)
funnel_rollup = pd.DataFrame(rows)
write(funnel_rollup, "funnel_rollup.parquet")


# ==================== 5. HOURLY ====================
pv_hour = (df[df["name"] == "Page View"]
           .groupby(["date_local", "hour_local"]).size()
           .reset_index(name="page_views"))
intent_hour = (df[df["name"].isin(INTENT_EVENTS)]
               .groupby(["date_local", "hour_local"]).size()
               .reset_index(name="intent_events"))
hourly = pv_hour.merge(intent_hour, on=["date_local", "hour_local"], how="left").fillna(0)
hourly["intent_events"] = hourly["intent_events"].astype(int)
write(hourly, "hourly.parquet")


# ==================== 6. DESTINATIONS ====================
dest = (df[df["destination"].notna() & (df["name"] == "Page View")]
        .groupby(["destination", "device_type", "utm_source"], dropna=False)
        .agg(views=("id", "count"),
             sessions=("session_id", "nunique"),
             users=("profile_id", "nunique"))
        .reset_index())
write(dest, "destinations.parquet")


# ==================== 7. UTM SOURCES ====================
utm_stats = (
    sessions.groupby(["utm_source", "device_type"], dropna=False)
    .agg(
        sessions=("session_id", "count"),
        atc_count=("had_add_to_cart", "sum"),
        order_count=("had_order", "sum"),
        success_count=("had_success", "sum"),
    )
    .reset_index()
)
utm_stats["atc_rate_pct"] = (utm_stats["atc_count"] / utm_stats["sessions"] * 100).round(3)
utm_stats["order_rate_pct"] = (utm_stats["order_count"] / utm_stats["sessions"] * 100).round(3)
write(utm_stats, "utm_sources.parquet")


# ==================== 8. DEVICE ====================
dev_stats = (
    sessions.groupby(["device_type", "os_name"], dropna=False)
    .agg(
        sessions=("session_id", "count"),
        atc_count=("had_add_to_cart", "sum"),
        order_count=("had_order", "sum"),
        avg_events=("num_events", "mean"),
        avg_duration_min=("session_duration_min", "mean"),
    )
    .reset_index()
)
dev_stats["atc_rate_pct"] = (dev_stats["atc_count"] / dev_stats["sessions"] * 100).round(3)
dev_stats["order_rate_pct"] = (dev_stats["order_count"] / dev_stats["sessions"] * 100).round(3)
dev_stats["avg_events"] = dev_stats["avg_events"].round(2)
dev_stats["avg_duration_min"] = dev_stats["avg_duration_min"].round(2)
write(dev_stats, "device.parquet")


# ==================== 9. TOUR CONVERSION (per user × tour) ====================
tv = df[(df["name"] == "Page View")
        & df["tour_pid"].notna()
        & ~df["link"].isin(HOMEPAGE)].copy()
tv["valid_d"] = tv["duration_sec"].where(
    (tv["duration_sec"] > 0) & (tv["duration_sec"] <= 1800))

tour_conv = (
    tv.groupby(["profile_id", "tour_pid"])
    .agg(views=("id", "count"),
         total_time=("valid_d", "sum"),
         avg_time=("valid_d", "mean"),
         tour_name=("context_page_ld_name", "first"),
         last_visit=("create_ts", "max"))
    .reset_index()
)
tour_conv["last_visit"] = pd.to_datetime(
    tour_conv["last_visit"], utc=True).dt.tz_localize(None)

# Flag intent / booked
intent_pairs = set(zip(
    df.loc[df["name"].isin(INTENT_EVENTS), "profile_id"],
    df.loc[df["name"].isin(INTENT_EVENTS), "tour_pid"]))
booking_pairs = set(zip(
    df.loc[df["name"] == "Order Booking", "profile_id"],
    df.loc[df["name"] == "Order Booking", "tour_pid"]))
tour_conv["intent"] = [(p, t) in intent_pairs
                       for p, t in zip(tour_conv["profile_id"], tour_conv["tour_pid"])]
tour_conv["booked"] = [(p, t) in booking_pairs
                       for p, t in zip(tour_conv["profile_id"], tour_conv["tour_pid"])]

# Lấy first utm_source + device_type của profile (để filter)
profile_attrs = sessions.groupby("profile_id").agg(
    utm_source=("utm_source", "first"),
    device_type=("device_type", "first"),
    geo_country=("geo_country", "first"),
).reset_index()
tour_conv = tour_conv.merge(profile_attrs, on="profile_id", how="left")
write(tour_conv, "tour_conversion.parquet")


# ==================== 10. EVENT DURATION ====================
valid_d = (df["duration_sec"] > 0) & (df["duration_sec"] <= 1800)
ev_time = (
    df[valid_d].groupby("name")
    .agg(events_measured=("id", "count"),
         avg_sec=("duration_sec", "mean"),
         median_sec=("duration_sec", "median"),
         p90_sec=("duration_sec", lambda s: s.quantile(0.9)),
         total_sec=("duration_sec", "sum"))
    .reset_index()
)
total_count = df.groupby("name").size().reset_index(name="events_total")
ev_time = total_count.merge(ev_time, on="name", how="left").fillna(0)
ev_time["coverage_pct"] = (
    ev_time["events_measured"] / ev_time["events_total"] * 100).round(1)
for c in ["avg_sec", "median_sec", "p90_sec", "total_sec"]:
    ev_time[c] = ev_time[c].round(2)
write(ev_time, "event_duration.parquet")


# ==================== 11. TOP TOURS (top 200) ====================
top_tours = (
    tour_conv.groupby(["tour_pid", "tour_name"])
    .agg(
        total_views=("views", "sum"),
        unique_users=("profile_id", "nunique"),
        total_time=("total_time", "sum"),
        intents=("intent", "sum"),
        bookings=("booked", "sum"),
    )
    .reset_index()
    .sort_values("total_views", ascending=False)
    .head(200)
)
top_tours["conv_pct"] = (
    top_tours["intents"] / top_tours["unique_users"] * 100).round(2)
write(top_tours, "top_tours.parquet")


# ==================== SUMMARY ====================
total_size = sum(f.stat().st_size for f in AGG.glob("*.parquet"))
meta_size = (AGG / "meta.json").stat().st_size
print(f"\n✓ Tổng {len(list(AGG.glob('*.parquet')))} parquet + 1 meta.json  "
      f"= {(total_size + meta_size) / 1024:.1f} KB")

# Show summary via DuckDB
print("\nDashboard sẽ query các file trên. Smoke test với DuckDB:")
con = duckdb.connect()
con.execute(f"CREATE VIEW sessions AS SELECT * FROM '{AGG / 'sessions.parquet'}'")
con.execute(f"CREATE VIEW tour AS SELECT * FROM '{AGG / 'tour_conversion.parquet'}'")
print(con.execute("SELECT COUNT(*) as n_sessions, SUM(had_add_to_cart::INT) as atc FROM sessions").fetchone())
print(con.execute("SELECT COUNT(*) as pairs, SUM(intent::INT) as intents FROM tour").fetchone())
