# Hướng dẫn migrate dashboard lên 7M data

Khi bạn có file Excel/CSV **7 triệu rows**, dashboard hiện tại có thể scale mà không cần rewrite code. Làm theo 5 bước dưới đây.

## Tóm tắt ngắn gọn

```
Excel 7M rows  →  process_full.py  →  data/events_clean.parquet  (~500MB-1GB)
                                               │
                                               ├─→ upload lên Hugging Face Hub (miễn phí)
                                               │
                                               └─→ etl_aggregates.py  →  data/aggregates/*.parquet (~200MB)
                                                                            │
                                                                            └─→ commit 1 phần nhẹ vào repo, phần nặng lên HF Hub

Streamlit Cloud app  →  đọc aggregates nhẹ + DuckDB query HF Hub Parquet khi drill-down
```

Tổng thời gian: ~30 phút setup 1 lần.

---

## Bước 1 — Chuẩn bị input

Đặt file `full_data.xlsx` (hoặc `.csv`) vào thư mục `tien_ghe_task/`. File phải có cùng schema với file hiện tại (179 cột chuẩn event-tracking).

```bash
cd /Users/jamesgatsby/tien_ghe_task
ls full_data.xlsx   # check có chưa
```

---

## Bước 2 — Chạy ETL

```bash
# Clean + fix encoding + export Parquet
python3 process_full.py

# Sinh aggregates (tất cả pre-computed stats)
python3 etl_aggregates.py
```

**Kết quả:**
- `data/events_clean.parquet` — file chính, **~500MB-1GB** cho 7M rows
- `data/aggregates/*.parquet` — 11 files pre-aggregated, **~100-250MB** tổng

Kiểm tra sizes:
```bash
ls -lh data/events_clean.parquet
du -sh data/aggregates
```

Nếu `events_clean.parquet` > **100 MB** → không push GitHub được, phải lên HF Hub (bước 3).

---

## Bước 3 — Upload raw Parquet lên Hugging Face Hub (free, CDN)

### 3.1. Tạo account + dataset

1. Đăng ký tại https://huggingface.co (miễn phí, không credit card).
2. Vào **New Dataset** → đặt tên (ví dụ `vietravel-events`), chọn **Public**.
3. Ghi lại URL dataset: `https://huggingface.co/datasets/<username>/vietravel-events`

### 3.2. Tạo access token

1. Settings → Access Tokens → **New token** → chọn role `write`.
2. Copy token (dạng `hf_xxxxxxxxxxx`).

### 3.3. Upload file

Cài CLI:
```bash
pip install huggingface_hub
```

Login + upload:
```bash
huggingface-cli login   # paste token khi được hỏi

huggingface-cli upload <username>/vietravel-events \
  data/events_clean.parquet \
  events_clean.parquet \
  --repo-type=dataset
```

Sau khi upload, URL trực tiếp file Parquet là:
```
https://huggingface.co/datasets/<username>/vietravel-events/resolve/main/events_clean.parquet
```

Test URL trên browser, nếu download được là OK.

---

## Bước 4 — Cấu hình dashboard dùng URL HF Hub

Dashboard đã support via environment variable `RAW_PARQUET_URL`. Không cần sửa code.

### 4.1. Local test

```bash
export RAW_PARQUET_URL="https://huggingface.co/datasets/<username>/vietravel-events/resolve/main/events_clean.parquet"
streamlit run dashboard.py
```

Session Explorer giờ sẽ query Parquet từ HF Hub thay vì local file. Load lần đầu chậm ~2-3s (HTTP range request), lần sau cached.

### 4.2. Streamlit Cloud

1. Vào app trên share.streamlit.io → **Settings** → **Secrets** (hoặc advanced env vars).
2. Thêm:
   ```toml
   RAW_PARQUET_URL = "https://huggingface.co/datasets/<username>/vietravel-events/resolve/main/events_clean.parquet"
   ```
3. Save → app tự restart.

---

## Bước 5 — Xử lý aggregates lớn

Với 7M rows, aggregate files có thể > 50MB:
- `sessions.parquet` — ước tính ~80-150 MB (1-2M sessions)
- `tour_conversion.parquet` — ước tính ~50-100 MB (1-2M user-tour pairs)
- Các file khác (patterns, funnel_rollup, hourly…) — vẫn < 5 MB, commit bình thường

### Option A — Upload luôn lên HF Hub (khuyến nghị)

```bash
huggingface-cli upload <username>/vietravel-events \
  data/aggregates/ aggregates/ \
  --repo-type=dataset
```

Sửa `dashboard.py` để load aggregates từ URL:

```python
# Ở đầu dashboard.py, thay đoạn load_agg:
AGG_URL = os.environ.get("AGG_URL",
    "https://huggingface.co/datasets/<username>/vietravel-events/resolve/main/aggregates")

@st.cache_data(show_spinner="Loading aggregates…")
def load_agg(name: str) -> pd.DataFrame:
    if AGG_URL.startswith("http"):
        return pd.read_parquet(f"{AGG_URL}/{name}")
    return pd.read_parquet(AGG / name)
```

### Option B — Chỉ commit rollups nhẹ, gitignore files nặng

Thêm vào `.gitignore`:
```
data/events_clean.parquet
data/aggregates/sessions.parquet
data/aggregates/session_pattern.parquet
data/aggregates/tour_conversion.parquet
```

Các file nặng này sẽ sinh lại từ `etl_aggregates.py` (chạy offline) + upload HF Hub, còn các file rollup nhỏ (`patterns.parquet`, `funnel_rollup.parquet`, `hourly.parquet`, `destinations.parquet`, `utm_sources.parquet`, `device.parquet`, `event_duration.parquet`, `top_tours.parquet`, `meta.json`) vẫn trong repo để deploy instant.

---

## Performance expectations sau migrate

| Metric | 14K rows (hiện tại) | 7M rows (sau migrate) |
|---|---|---|
| App cold start (Streamlit Cloud) | 3-5s | 8-15s |
| Filter change (device/utm/country) | <200ms | <500ms |
| Tab switch | instant | instant |
| Session Explorer drill-down | 100ms | 1-3s (HF Hub HTTP) |
| Memory usage | ~100 MB | ~500-800 MB (trong giới hạn 1GB) |

Nếu memory exceed 1GB trên Streamlit Cloud:
- Move thêm aggregates sang HF Hub (Option A ở trên)
- Hoặc upgrade lên **Hugging Face Spaces** (free 16GB RAM thay vì 1GB)

---

## Troubleshooting

### `FileNotFoundError: events_clean.parquet`
ETL chưa chạy. Chạy `python3 process_full.py` trước.

### Dashboard OOM (out of memory) trên Streamlit Cloud
- Upload `sessions.parquet` + `tour_conversion.parquet` lên HF Hub (Option A).
- Hoặc chuyển sang HF Spaces (16GB RAM free).

### Session Explorer báo lỗi HTTP 404 / timeout
- Check URL HF Hub có đúng không (test trên browser).
- Check dataset public, không private.
- Nếu private: cần thêm HF token vào environment `HF_TOKEN`.

### Aggregates ra khác với dashboard cũ
Dashboard hiện tại compute trực tiếp từ raw CSV theo filter. Aggregates pre-compute *không* có mọi filter combination. Với các filter phổ biến (device × utm × country × date) thì kết quả **phải khớp**. Nếu thấy lệch:
1. Check `etl_aggregates.py` có chạy đúng input không.
2. Rerun `python3 etl_aggregates.py` sau mỗi lần update raw Parquet.

---

## Scale xa hơn: > 50M rows

Nếu data tiếp tục tăng lên **50M+ rows**:

1. **Chuyển sang DuckDB/Postgres native**: bỏ pre-aggregate pandas, query SQL trực tiếp.
2. **MotherDuck** (https://motherduck.com) — DuckDB cloud, free tier 10GB, zero config.
3. **Chia nhỏ Parquet** theo tháng/quý → DuckDB `read_parquet('data/*/events.parquet')` tự union.
4. Cân nhắc paid Streamlit Teams ($20/user/mo) để có 4GB RAM.

Plan này không cần thiết ở mức 7M, nhưng biết trước để tương lai không rework.
