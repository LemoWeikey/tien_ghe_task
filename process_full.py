"""
Xử lý full_data.csv:
  1) Fix encoding tiếng Việt.
  2) Drop các cột hạ tầng / HTTP headers / metadata nội bộ không phục vụ phân tích.
  3) Tính duration người dùng ở lại trên từng link:
        duration = create[next_event_in_same_session] - create[current_event]
     rồi aggregate theo link.
"""

import re
import sys
import pandas as pd
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)

# Input: ưu tiên CSV, chấp nhận XLSX cho input 7M từ user
SRC_CANDIDATES = [BASE / "full_data.csv", BASE / "full_data.xlsx"]
SRC = next((p for p in SRC_CANDIDATES if p.exists()), None)

OUT_CLEAN = BASE / "full_data_clean.csv"
OUT_PARQUET = DATA_DIR / "events_clean.parquet"          # file chính cho dashboard + DuckDB
OUT_EVENT_DUR = BASE / "full_data_event_duration.csv"
OUT_LINK_DUR = BASE / "full_data_link_duration.csv"


# ---------- Fix tiếng Việt ----------
XML_ESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _to_bytes(s):
    out = bytearray()
    for ch in s:
        o = ord(ch)
        if o < 256:
            out.append(o)
        else:
            try:
                out.extend(ch.encode("cp1252"))
            except UnicodeEncodeError:
                return None
    return bytes(out)


def fix_vi(val):
    if not isinstance(val, str):
        return val
    s = XML_ESC.sub(lambda m: chr(int(m.group(1), 16)), val)
    for _ in range(3):
        b = _to_bytes(s)
        if b is None:
            break
        try:
            new = b.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            break
        if new == s:
            break
        s = new
    return s


# ---------- Chọn cột phân tích ----------
# Giữ lại những cột thực sự có giá trị phân tích hành vi / sản phẩm / attribution.
KEEP_COLS = [
    # Event & identity
    "id", "name", "type", "hit_name", "journey_state",
    "session_id", "profile_id",
    # Thời gian (cần cho duration)
    "create", "insert", "session_start", "session_duration",
    # Page / link
    "context_page_url", "context_page_path", "context_page_title",
    "context_page_referer_host",
    "hit_url", "hit_referer",
    # Sản phẩm tour (từ JSON-LD)
    "context_page_ld_category",
    "context_page_ld_name",
    "context_page_ld_productID",
    "context_page_ld_sku",
    "context_page_ld_offers_price",
    "context_page_ld_offers_priceCurrency",
    "context_page_ld_offers_availability",
    "context_page_ld_aggregateRating_ratingValue",
    "context_page_ld_aggregateRating_reviewCount",
    # Attribution
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    # Device / app
    "device_type", "device_name", "device_brand", "device_model",
    "device_resolution", "device_orientation",
    "os_name", "os_version",
    "app_name", "app_version",
    # Geo (mức city/country đủ dùng)
    "device_geo_country_code", "device_geo_country_name", "device_geo_city",
    # Properties (search / booking / tour interaction)
    "properties_tour_code", "properties_tour_name", "properties_tour_price",
    "properties_tourCode", "properties_tourType",
    "properties_code", "properties_name", "properties_page_code",
    "properties_button",
    "properties_bookingNo", "properties_bookingAmount", "properties_paymentType",
    "properties_customerNo",
    "properties_fullName", "properties_gender",
    "properties_contactInfo_contactName", "properties_contactInfo_email",
    "properties_contactInfo_phoneNumber", "properties_contactInfo_address",
    "properties_contactInfo_note",
]

# ---------- Load ----------
if SRC is None:
    sys.exit(
        "❌ Không tìm thấy input. Đặt 'full_data.csv' hoặc 'full_data.xlsx' cùng thư mục rồi chạy lại."
    )

print(f"Reading {SRC.name} ...")
if SRC.suffix.lower() == ".xlsx":
    df = pd.read_excel(SRC)
else:
    df = pd.read_csv(SRC, low_memory=False)
print(f"  rows={len(df)}  cols={len(df.columns)}")

# Chỉ giữ cột cần (bỏ request_headers_*, metadata_*, cf-*, geo chi tiết...)
missing = [c for c in KEEP_COLS if c not in df.columns]
if missing:
    print("Thiếu cột:", missing)
df = df[[c for c in KEEP_COLS if c in df.columns]].copy()
print(f"  after drop cols={len(df.columns)}")

# ---------- Fix tiếng Việt trên mọi cột text ----------
print("Fixing Vietnamese encoding ...")
for c in df.select_dtypes(include=["object"]).columns:
    df[c] = df[c].map(fix_vi)

# ---------- Duration ----------
print("Computing duration ...")
df["create_ts"] = pd.to_datetime(df["create"], utc=True, errors="coerce")

# Chuẩn hoá link: bỏ fragment để nhóm cùng URL
def canon_link(u):
    if not isinstance(u, str):
        return None
    return u.split("#")[0]


df["link"] = df["context_page_url"].map(canon_link)

# Sort theo session_id + thời gian, lấy next timestamp trong cùng session
df = df.sort_values(["session_id", "create_ts"]).reset_index(drop=True)
df["next_ts"] = df.groupby("session_id")["create_ts"].shift(-1)
df["duration_sec"] = (df["next_ts"] - df["create_ts"]).dt.total_seconds()

# Sanity: duration phải >= 0. Giá trị âm (nếu có) loại bỏ. Cap các giá trị quá lớn
# (người dùng bỏ tab, session không thực sự active) thành "unbounded".
# Ở đây giữ nguyên giá trị thô cho event-level; aggregate sẽ lọc.
MAX_ACTIVE_SEC = 30 * 60  # 30 phút: sau đó coi như user bỏ đi, không đếm

# Loại homepage: không có giá trị phân tích hành vi (chỉ là landing vô thưởng vô phạt).
HOMEPAGE_URLS = {
    "https://travel.com.vn/",
    "https://travel.com.vn",
    "http://travel.com.vn/",
    "http://travel.com.vn",
}

df_active = df[
    (df["duration_sec"] > 0)
    & (df["duration_sec"] <= MAX_ACTIVE_SEC)
    & (~df["link"].isin(HOMEPAGE_URLS))
].copy()

# Loại PII khỏi output để an toàn khi push public
PII_COLS = [
    "properties_contactInfo_email",
    "properties_contactInfo_phoneNumber",
    "properties_contactInfo_address",
    "properties_contactInfo_contactName",
    "properties_contactInfo_note",
    "properties_fullName",
    "properties_gender",
]
for c in PII_COLS:
    if c in df.columns:
        df = df.drop(columns=c)
    if c in df_active.columns:
        df_active = df_active.drop(columns=c)

# ---------- Xuất ----------
# 1a) CSV (backward compatibility)
out_df = df.drop(columns=["next_ts"])
out_df.to_csv(OUT_CLEAN, index=False)
print(f"  wrote {OUT_CLEAN.name}  rows={len(out_df)}  cols={out_df.shape[1]}")

# 1b) Parquet (file chính dùng cho dashboard + DuckDB drill-down)
out_df.to_parquet(OUT_PARQUET, compression="zstd", index=False)
size_mb = OUT_PARQUET.stat().st_size / 1024 / 1024
print(f"  wrote {OUT_PARQUET.relative_to(BASE)}  "
      f"rows={len(out_df)}  size={size_mb:.1f} MB (zstd)")

# 2) Duration từng event (chỉ những event có duration hợp lệ)
event_cols = [
    "session_id", "profile_id", "name", "type", "journey_state",
    "link", "context_page_title",
    "context_page_ld_name", "context_page_ld_productID",
    "create_ts", "duration_sec",
    "utm_source", "utm_medium", "utm_campaign",
    "device_type", "device_geo_country_code", "device_geo_city",
]
df_active[event_cols].to_csv(OUT_EVENT_DUR, index=False)
print(f"  wrote {OUT_EVENT_DUR.name}  rows={len(df_active)}")

# 3) Aggregate theo link
link_stats = (
    df_active.groupby("link")
    .agg(
        views=("link", "size"),
        unique_sessions=("session_id", "nunique"),
        unique_users=("profile_id", "nunique"),
        total_time_sec=("duration_sec", "sum"),
        avg_time_sec=("duration_sec", "mean"),
        median_time_sec=("duration_sec", "median"),
        p90_time_sec=("duration_sec", lambda s: s.quantile(0.9)),
    )
    .reset_index()
    .sort_values("views", ascending=False)
)
link_stats.to_csv(OUT_LINK_DUR, index=False)
print(f"  wrote {OUT_LINK_DUR.name}  rows={len(link_stats)}")

# ---------- Summary in ra terminal ----------
print("\n===== SUMMARY =====")
total_rows = len(df)
computable = df["duration_sec"].notna().sum()
print(f"Total events: {total_rows}")
print(f"Events có duration tính được (có event kế tiếp cùng session): {computable} "
      f"({computable/total_rows*100:.1f}%)")
print(f"Events sau khi lọc 0 < duration ≤ {MAX_ACTIVE_SEC}s: {len(df_active)}")
print(f"Unique sessions: {df['session_id'].nunique()}")
print(f"Unique profiles: {df['profile_id'].nunique()}")
print(f"Unique links: {df['link'].nunique()}")
print()
print("Top 10 links by views (avg time_on_page):")
print(link_stats.head(10)[["link", "views", "unique_sessions",
                           "avg_time_sec", "median_time_sec"]].to_string(index=False))
print()
print("Events per session distribution:")
eps = df.groupby("session_id").size()
print(f"  mean={eps.mean():.2f}  median={eps.median()}  max={eps.max()}  "
      f"sessions_with_1_event={(eps==1).sum()}/{len(eps)} "
      f"({(eps==1).sum()/len(eps)*100:.1f}%)")
