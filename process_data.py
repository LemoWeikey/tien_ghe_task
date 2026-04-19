"""
Script xử lý data_tien_ghe.xlsx:
  Task 1: Fix lỗi encoding tiếng Việt (mojibake + _x0090_ escapes)
  Task 2: Xuất tài liệu mô tả ý nghĩa từng cột (Excel)
  Task 3: Phân tích các cột time -> có tính được duration người dùng ở lại link không?
"""

import re
import pandas as pd
from pathlib import Path

BASE = Path("/Users/jamesgatsby/tien_ghe_task")
SRC = BASE / "data_tien_ghe.xlsx"
OUT_FIXED = BASE / "data_tien_ghe_fixed.xlsx"
OUT_DICT = BASE / "data_tien_ghe_dictionary.xlsx"
OUT_DURATION = BASE / "data_tien_ghe_duration_analysis.xlsx"


# ----------------- Task 1: sửa encoding -----------------
XML_ESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _str_to_bytes(s):
    """
    Chuỗi hiện tại là các byte gốc được decode nhầm qua cp1252/latin-1 mix.
    Ta cần dựng lại bytes gốc: char < 256 -> dùng code point làm byte;
    char cao hơn (ví dụ ‘ ’ “ ” – là smart-quote cp1252) -> encode cp1252.
    """
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
    """
    Lỗi kép:
      (a) _xHHHH_ escape của Excel (ví dụ _x0090_ -> U+0090 = byte 0x90)
      (b) Bytes gốc UTF-8 bị decode nhầm thành cp1252/latin-1 -> hiển thị mojibake.
    Fix: resolve escape -> chuyển ngược về bytes -> decode UTF-8. Lặp tới khi ổn định.
    """
    if not isinstance(val, str):
        return val
    s = XML_ESC.sub(lambda m: chr(int(m.group(1), 16)), val)
    for _ in range(3):
        b = _str_to_bytes(s)
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


df = pd.read_excel(SRC)

# Fix tất cả object columns
for col in df.select_dtypes(include=["object"]).columns:
    df[col] = df[col].map(fix_vi)

df.to_excel(OUT_FIXED, index=False)
print(f"[Task 1] Đã ghi file tiếng Việt chuẩn: {OUT_FIXED.name}")


# ----------------- Task 2: dictionary cột -----------------
# Mô tả ý nghĩa cho từng nhóm cột (đúc kết từ schema event-tracking / CDP chuẩn)
COL_DESC = {
    # app
    "app_bot": "Cờ đánh dấu traffic từ bot",
    "app_language": "Ngôn ngữ trình duyệt/app",
    "app_name": "Tên trình duyệt hoặc app (Chrome, Mobile Safari, Facebook in-app browser…)",
    "app_resolution": "Độ phân giải cửa sổ app",
    "app_type": "Loại app: browser / mobile / …",
    "app_version": "Phiên bản app/trình duyệt",
    "config_fire": "Cờ nội bộ: event có được kích hoạt không",
    "context_ga": "Google Analytics client id (_ga cookie)",
    "context_page_hash": "Phần hash (#...) của URL trang",
    "context_page_history_length": "Chiều dài history của tab (window.history.length)",
    # JSON-LD trong trang
    "context_page_ld_aggregateRating_ratingValue": "Điểm rating tổng (schema.org) của trang tour",
    "context_page_ld_aggregateRating_reviewCount": "Số review (schema.org)",
    "context_page_ld_aggregateRating_type": "schema.org type = AggregateRating",
    "context_page_ld_author_name": "Tên tác giả (schema.org)",
    "context_page_ld_author_type": "Loại author (Person/Organization)",
    "context_page_ld_brand_name": "Tên brand (Vietravel)",
    "context_page_ld_brand_type": "schema.org Brand",
    "context_page_ld_category": "Danh mục sản phẩm (tour quốc tế, tour trong nước…)",
    "context_page_ld_context": "Namespace JSON-LD (https://schema.org)",
    "context_page_ld_description": "Mô tả ngắn sản phẩm/trang",
    "context_page_ld_headline": "Tiêu đề chính JSON-LD",
    "context_page_ld_image": "URL ảnh chính sản phẩm",
    "context_page_ld_image_type": "Type ImageObject",
    "context_page_ld_image_url": "URL ảnh phụ",
    "context_page_ld_keywords": "Keywords SEO JSON-LD",
    "context_page_ld_mainEntityOfPage_id": "ID của main entity",
    "context_page_ld_mainEntityOfPage_type": "Type WebPage",
    "context_page_ld_name": "Tên tour / tên sản phẩm",
    "context_page_ld_offers_availability": "Tình trạng còn chỗ (InStock/OutOfStock)",
    "context_page_ld_offers_itemCondition": "Tình trạng sản phẩm (NewCondition…)",
    "context_page_ld_offers_price": "Giá tour",
    "context_page_ld_offers_priceCurrency": "Đơn vị tiền tệ (VND)",
    "context_page_ld_offers_seller_name": "Người bán (Vietravel)",
    "context_page_ld_offers_seller_type": "Loại seller",
    "context_page_ld_offers_type": "schema.org Offer",
    "context_page_ld_offers_url": "URL offer",
    "context_page_ld_productID": "ID sản phẩm tour trong hệ thống",
    "context_page_ld_publisher_logo_type": "Type logo publisher",
    "context_page_ld_publisher_logo_url": "URL logo publisher",
    "context_page_ld_publisher_name": "Tên publisher",
    "context_page_ld_publisher_type": "Loại publisher",
    "context_page_ld_review_author_name": "Tên người review",
    "context_page_ld_review_author_type": "Loại author review",
    "context_page_ld_review_reviewRating_bestRating": "Điểm tối đa của thang rating",
    "context_page_ld_review_reviewRating_ratingValue": "Điểm rating của review",
    "context_page_ld_review_reviewRating_type": "Type Rating",
    "context_page_ld_review_type": "Type Review",
    "context_page_ld_sku": "Mã SKU sản phẩm",
    "context_page_ld_slogan": "Slogan",
    "context_page_ld_type": "Type của entity (Product)",
    # page
    "context_page_path": "Đường dẫn trang (pathname)",
    "context_page_referer_host": "Host của trang referer",
    "context_page_referer_query": "Query string của referer",
    "context_page_title": "Tiêu đề trang (document.title)",
    "context_page_url": "URL đầy đủ của trang lúc sự kiện xảy ra",
    # timestamps
    "create": "Thời điểm event được tạo ở client (ISO-8601 UTC)",
    "insert": "Thời điểm record được ghi vào hệ thống (ISO-8601 UTC)",
    # contact
    "data_contact_email_main": "Email chính của khách (profile-level)",
    # device
    "device_brand": "Hãng thiết bị (Apple, Samsung…)",
    "device_color_depth": "Độ sâu màu màn hình (bit)",
    "device_geo_city": "Thành phố theo geo-IP",
    "device_geo_country_code": "Mã quốc gia (ISO)",
    "device_geo_country_name": "Tên quốc gia",
    "device_geo_county": "Quận/huyện theo geo-IP",
    "device_geo_latitude": "Vĩ độ geo-IP",
    "device_geo_location": "Toạ độ (lat,lon) geo-IP",
    "device_geo_longitude": "Kinh độ geo-IP",
    "device_geo_postal": "Mã bưu chính",
    "device_ip": "IP thiết bị (qua Cloudflare)",
    "device_model": "Model thiết bị (iPhone15,4…)",
    "device_name": "Tên thiết bị (iPhone, Other)",
    "device_orientation": "Hướng màn hình (portrait/landscape)",
    "device_resolution": "Độ phân giải màn hình thiết bị",
    "device_touch": "Thiết bị có touch (1.0 = có)",
    "device_type": "Loại thiết bị (mobile/pc/tablet)",
    # hit
    "hit_name": "Tên trang được hit",
    "hit_referer": "URL referer",
    "hit_url": "URL đầy đủ của hit",
    # identifiers
    "id": "ID duy nhất của event (UUID)",
    "journey_state": "Trạng thái khách trong customer journey (awareness…)",
    # metadata
    "metadata_channel": "Kênh dữ liệu (travel-web)",
    "metadata_debug": "Cờ debug",
    "metadata_error": "Có lỗi khi xử lý event không",
    "metadata_instance_id": "ID instance thu thập",
    "metadata_ip": "IP ghi nhận phía metadata",
    "metadata_merge": "Đã merge profile chưa",
    "metadata_processed_by_flows": "Các flow đã xử lý event này",
    "metadata_processed_by_rules": "Các rule đã xử lý",
    "metadata_processed_by_third_party": "Bên thứ 3 đã xử lý",
    "metadata_profile_less": "Event không gắn profile",
    "metadata_status": "Trạng thái xử lý (collected/processed…)",
    "metadata_time_process_time": "Thời gian xử lý (ms)",
    "metadata_time_total_time": "Tổng thời gian (ms)",
    "metadata_time_update": "Timestamp update record",
    "metadata_valid": "Event có hợp lệ không",
    "metadata_warning": "Có warning không",
    # event
    "name": "Tên event (Page View, Search Tour…)",
    "os_name": "Hệ điều hành (iOS, Windows…)",
    "os_version": "Phiên bản OS",
    "profile_id": "ID profile người dùng (anon/real)",
    # properties
    "properties_address": "Địa chỉ khách nhập",
    "properties_bookingAmount": "Số tiền booking",
    "properties_bookingNo": "Mã booking",
    "properties_button": "Tên nút được click",
    "properties_code": "Mã (tour code/search code…)",
    "properties_contactInfo_address": "Địa chỉ liên hệ",
    "properties_contactInfo_contactName": "Tên liên hệ",
    "properties_contactInfo_email": "Email liên hệ",
    "properties_contactInfo_note": "Ghi chú liên hệ",
    "properties_contactInfo_phoneNumber": "SĐT liên hệ",
    "properties_context_page_origin": "Origin trang gửi event",
    "properties_context_page_path": "Path trang gửi event",
    "properties_context_page_title": "Tiêu đề trang gửi event",
    "properties_context_page_url": "URL trang gửi event",
    "properties_customerNo": "Mã khách hàng",
    "properties_email_main": "Email chính (property)",
    "properties_fullName": "Họ tên đầy đủ",
    "properties_gender": "Giới tính",
    "properties_idCard": "Số CMND/CCCD",
    "properties_image": "URL ảnh gắn với event",
    "properties_name": "Tên search/tour khách tìm",
    "properties_page_code": "Mã trang",
    "properties_paymentType": "Hình thức thanh toán",
    "properties_pinCode": "Mã PIN",
    "properties_tourCode": "Mã tour (properties)",
    "properties_tourType": "Loại tour",
    "properties_tour_code": "Mã tour (snake_case)",
    "properties_tour_name": "Tên tour",
    "properties_tour_option_no": "Mã option tour",
    "properties_tour_price": "Giá tour",
    # request headers (HTTP)
    "request_headers_accept": "Header Accept",
    "request_headers_accept-encoding": "Header Accept-Encoding",
    "request_headers_accept-language": "Header Accept-Language",
    "request_headers_cache-control": "Header Cache-Control",
    "request_headers_cdn-loop": "Header CDN-Loop (Cloudflare)",
    "request_headers_cf-connecting-ip": "IP gốc client qua Cloudflare",
    "request_headers_cf-ipcountry": "Quốc gia do Cloudflare phát hiện",
    "request_headers_cf-ray": "CF-Ray ID (trace)",
    "request_headers_cf-visitor": "JSON visitor info (scheme)",
    "request_headers_content-length": "Độ dài body",
    "request_headers_content-type": "Content-Type",
    "request_headers_dnt": "Do Not Track",
    "request_headers_host": "Host nhận request (track.vietravel.com)",
    "request_headers_origin": "Origin",
    "request_headers_pragma": "Header Pragma",
    "request_headers_priority": "Header Priority",
    "request_headers_referer": "Referer",
    "request_headers_save-data": "Header Save-Data",
    "request_headers_sec-ch-ua": "Client-Hints UA",
    "request_headers_sec-ch-ua-full-version-list": "UA version đầy đủ",
    "request_headers_sec-ch-ua-mobile": "UA mobile flag",
    "request_headers_sec-ch-ua-platform": "UA platform",
    "request_headers_sec-fetch-dest": "Sec-Fetch-Dest",
    "request_headers_sec-fetch-mode": "Sec-Fetch-Mode",
    "request_headers_sec-fetch-site": "Sec-Fetch-Site",
    "request_headers_sec-gpc": "Global Privacy Control",
    "request_headers_sec-purpose": "Sec-Purpose (prefetch)",
    "request_headers_user-agent": "Chuỗi User-Agent đầy đủ",
    "request_headers_x-forwarded-for": "X-Forwarded-For",
    "request_headers_x-forwarded-host": "X-Forwarded-Host",
    "request_headers_x-forwarded-port": "X-Forwarded-Port",
    "request_headers_x-forwarded-proto": "X-Forwarded-Proto",
    "request_headers_x-forwarded-server": "X-Forwarded-Server",
    "request_headers_x-real-ip": "X-Real-IP",
    "request_headers_x-requested-with": "X-Requested-With",
    "request_headers_x-timestamp": "Timestamp client gửi (epoch ms)",
    # session
    "session_duration": "Thời lượng session (tính sẵn) — trong file mẫu luôn = 0",
    "session_id": "ID session (UUID)",
    "session_start": "Thời điểm bắt đầu session",
    "session_tz": "Múi giờ session",
    # source / tags
    "source_id": "ID nguồn dữ liệu",
    "tags_count": "Số tag gán cho event",
    "tags_values": "Danh sách tag (JSON array)",
    "type": "Loại event (page-view, search-tour…)",
    # UTM
    "utm_campaign": "UTM campaign name",
    "utm_content": "UTM content (creative)",
    "utm_medium": "UTM medium (cpa, paid_social, zalo…)",
    "utm_source": "UTM source (DIGI_Google, DIGI_Facebook…)",
    "utm_term": "UTM term (keyword/ad id)",
}


def sample_val(col):
    """Lấy 1 giá trị non-null đầu tiên làm ví dụ"""
    s = df[col].dropna()
    if s.empty:
        return ""
    v = s.iloc[0]
    v = str(v)
    return v if len(v) <= 120 else v[:117] + "..."


dict_rows = []
for col in df.columns:
    dict_rows.append(
        {
            "column": col,
            "dtype": str(df[col].dtype),
            "non_null": int(df[col].notna().sum()),
            "nunique": int(df[col].nunique(dropna=True)),
            "meaning_vi": COL_DESC.get(col, "(chưa rõ – cần hỏi nguồn dữ liệu)"),
            "sample": sample_val(col),
        }
    )

dict_df = pd.DataFrame(dict_rows)
dict_df.to_excel(OUT_DICT, index=False)
print(f"[Task 2] Đã ghi dictionary: {OUT_DICT.name}")


# ----------------- Task 3: phân tích duration -----------------
# Chuẩn hoá thời gian
df["create_ts"] = pd.to_datetime(df["create"], utc=True, errors="coerce")
df["insert_ts"] = pd.to_datetime(df["insert"], utc=True, errors="coerce")
df["session_start_ts"] = pd.to_datetime(df["session_start"], errors="coerce")

# Link = URL trang không tính fragment / query
def canon_link(u):
    if not isinstance(u, str):
        return None
    u = u.split("#")[0]
    return u


df["link"] = df["context_page_url"].map(canon_link)

# Duration 1: thời gian giữa các event liên tiếp trong cùng session, sort theo create_ts
df_sorted = df.sort_values(["session_id", "create_ts"]).reset_index(drop=True)
df_sorted["next_ts"] = df_sorted.groupby("session_id")["create_ts"].shift(-1)
df_sorted["duration_on_link_sec"] = (
    (df_sorted["next_ts"] - df_sorted["create_ts"]).dt.total_seconds()
)

duration_view = df_sorted[
    [
        "session_id",
        "profile_id",
        "name",
        "link",
        "create_ts",
        "next_ts",
        "duration_on_link_sec",
    ]
].copy()
# Excel không hỗ trợ datetime có timezone -> strip tz
duration_view["create_ts"] = duration_view["create_ts"].dt.tz_localize(None)
duration_view["next_ts"] = duration_view["next_ts"].dt.tz_localize(None)

# Tổng hợp
session_counts = (
    df.groupby("session_id")
    .size()
    .reset_index(name="events_per_session")
    .sort_values("events_per_session", ascending=False)
)

# Kiểm tra tính sẵn của session_duration
sd_stats = df["session_duration"].describe()

with pd.ExcelWriter(OUT_DURATION) as w:
    duration_view.to_excel(w, sheet_name="per_event_duration", index=False)
    session_counts.to_excel(w, sheet_name="events_per_session", index=False)
    pd.DataFrame(
        {
            "metric": [
                "rows",
                "unique_session_id",
                "unique_profile_id",
                "unique_link",
                "session_duration_min",
                "session_duration_max",
                "session_duration_mean",
                "has_create_ts",
                "has_insert_ts",
                "has_session_start",
            ],
            "value": [
                len(df),
                df["session_id"].nunique(),
                df["profile_id"].nunique(),
                df["link"].nunique(),
                sd_stats["min"],
                sd_stats["max"],
                sd_stats["mean"],
                int(df["create_ts"].notna().sum()),
                int(df["insert_ts"].notna().sum()),
                int(df["session_start_ts"].notna().sum()),
            ],
        }
    ).to_excel(w, sheet_name="summary", index=False)

print(f"[Task 3] Đã ghi duration analysis: {OUT_DURATION.name}")

# In kết luận
print("\n===== KẾT LUẬN TASK 3 =====")
print(f"- Tất cả {len(df)} dòng đều thuộc {df['session_id'].nunique()} session khác nhau.")
print(f"- Cột session_duration có sẵn: min={sd_stats['min']}, max={sd_stats['max']} -> luôn = 0, KHÔNG dùng được trực tiếp.")
print("- Các cột thời gian dùng được: 'create' (ISO UTC ở client) và 'insert' (khi ghi DB).")
print("- Cách tính duration trên link: sort event theo (session_id, create) rồi lấy hiệu create[next] - create[current] -> thời gian user ở lại trang đó trước khi chuyển sang event/trang kế tiếp.")
print("- Hạn chế: event cuối trong mỗi session không có 'next' -> không tính được duration; chỉ đo được tới khi có event kế tiếp (không biết user rời trang nếu không còn event nào).")
