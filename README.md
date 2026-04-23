# Vietravel Analytics Dashboard

Dashboard phân tích hành vi khách hàng và tỷ lệ chốt tour dựa trên event tracking của travel.com.vn.

## Tính năng

- **Funnel analysis** — tỷ lệ chuyển đổi qua 5 bước (Page View → Booking Success)
- **Customer journey** — top event pattern, session explorer với timeline chi tiết
- **Tour conversion** — intent rate theo số views × thời gian, heatmap 2D
- **Destinations & UTM** — top điểm đến, hiệu quả kênh marketing
- **Device & Time patterns** — mobile/pc/tablet, decision hour theo giờ trong ngày

## Chạy local

```bash
# Cài dependencies
pip install -r requirements.txt

# (Lần đầu) sinh aggregates từ raw data
python3 process_full.py      # → data/events_clean.parquet
python3 etl_aggregates.py    # → data/aggregates/*.parquet

# Chạy dashboard
streamlit run dashboard.py
```

Mở http://localhost:8501.

## Scale lên 7M+ rows

Khi có dataset lớn (>100MB Parquet), xem [MIGRATION_7M.md](MIGRATION_7M.md) — 5 bước upload lên Hugging Face Hub và config dashboard dùng DuckDB query remote.

## Deploy lên Streamlit Community Cloud

1. Fork hoặc clone repo này về GitHub của bạn.
2. Vào [share.streamlit.io](https://share.streamlit.io) → đăng nhập bằng GitHub.
3. Click **"New app"** → chọn repo `tien_ghe_task`, branch `main`, file `dashboard.py`.
4. Click **Deploy** → chờ 2-3 phút.

Dashboard sẽ có URL dạng `https://<tên-app>.streamlit.app`.

## Cấu trúc file

| File | Mô tả |
|---|---|
| `dashboard.py` | Streamlit dashboard chính |
| `full_data_clean.csv` | Data đã fix encoding tiếng Việt, loại cột hạ tầng & PII |
| `process_full.py` | Script sinh `full_data_clean.csv` từ raw data |
| `analysis_journey.py` | Script sinh báo cáo Excel tổng hợp |
| `make_doc.py` | Script sinh file Word hướng dẫn dashboard |
| `Huong_dan_Dashboard.docx` | Hướng dẫn sử dụng dashboard chi tiết |
| `analysis_report.xlsx` | Báo cáo Excel 14 sheet từ phân tích |

## Data schema

Event tracking data với các nhóm cột chính:

- **Identity**: `id`, `session_id`, `profile_id`
- **Event**: `name`, `type`, `journey_state`
- **Time**: `create`, `insert`, `session_start`
- **Page**: `context_page_url`, `context_page_path`, `context_page_title`
- **Product (tour)**: `context_page_ld_name`, `context_page_ld_offers_price`, `tour_pid` (derived)
- **Attribution**: `utm_source`, `utm_medium`, `utm_campaign`
- **Device**: `device_type`, `os_name`, `app_name`
- **Geo**: `device_geo_country_code`, `device_geo_city`

Xem chi tiết ý nghĩa từng cột trong `data_tien_ghe_dictionary.xlsx`.
