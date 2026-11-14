# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT, tập trung vào Việt Nam và châu Âu:

- **Tìm việc** bằng ngôn ngữ tự nhiên (tiếng Việt, Anh, Thụy Điển).
- **Hỏi số liệu** thị trường tuyển dụng, ví dụ "Top 10 skills".
- **Đối chiếu CV** với công việc và chỉ ra kỹ năng còn thiếu.

Giao diện web **CareerLens** gom cả ba tính năng trên.

Stack: Python 3.12, FastAPI, PostgreSQL, Qdrant, LangGraph, ONNX MiniLM (chạy local), Docker Compose.

## Tính năng

| Tính năng | Cách hoạt động | Chi tiết |
|---|---|---|
| **Nạp dữ liệu** | Nạp tin từ JobTech, Remotive, ITviec và trang tuyển dụng của các công ty có văn phòng ở Việt Nam. Trích kỹ năng kèm bằng chứng, có phân loại bắt buộc / ưu tiên / phủ định. Trích thêm seniority, số năm kinh nghiệm, lương và hạn nộp. | [INGESTION](docs/INGESTION.md) |
| **Tìm kiếm** | Mặc định **hybrid**: kết hợp từ khóa (BM25) với tìm theo nghĩa (embeddings), trọng số 2 : 1. Nếu vector index chưa sẵn sàng thì tự quay về BM25. | [RETRIEVAL](docs/RETRIEVAL.md) |
| **Hỏi dữ liệu** (`/ask`) | Định tuyến câu hỏi sang tìm việc, thống kê hoặc yêu cầu hỏi lại cho rõ. Câu hỏi phổ biến chạy theo mẫu có sẵn, không cần model. SQL do model viết phải qua allowlist và chạy bằng tài khoản chỉ đọc. | [ANALYTICS](docs/ANALYTICS.md) |
| **Đối chiếu CV** | Upload CV dạng PDF có text. Trích kỹ năng, tìm việc phù hợp, chỉ ra kỹ năng job cần mà chưa thấy trong CV. Chạy hoàn toàn local, không lưu file PDF, profile tự hết hạn sau 1 giờ. | [CV_MATCHING](docs/CV_MATCHING.md) |
| **Vận hành** | Password gate, quota theo client, cấu hình production (Compose + Caddy), backup và restore. Chưa deploy public. | [OPERATIONS](docs/OPERATIONS.md) |

## Chạy nhanh

Cần Docker Desktop (hoặc Docker Engine) và Docker Compose v2.

```bash
git clone https://github.com/bachthao4321/ai-career-intelligence.git
cd ai-career-intelligence
cp .env.example .env
docker compose up --build --wait --wait-timeout 120
```

- Giao diện: <http://localhost:8000/>
- API docs: <http://localhost:8000/docs>
- Kiểm tra trạng thái: `curl localhost:8000/ready`

Dừng bằng `docker compose down`. Dữ liệu vẫn được giữ lại.

## Nạp dữ liệu

Checkout mới chưa có tin tuyển dụng nào. Có thể thử bằng dữ liệu giả lập, hoặc lấy dữ liệu thật từ JobTech:

```bash
uv sync --frozen

# Dữ liệu giả lập
uv run --frozen python -m app.ingestion.cli import examples/jobs.synthetic.json

# Dữ liệu thật từ JobTech: fetch in ra đường dẫn snapshot để import
uv run --frozen python -m app.ingestion.cli fetch jobtech --limit 150
uv run --frozen python -m app.ingestion.cli import data/raw/jobtech-YYYY-MM-DD.json

# Bổ sung thông tin (seniority, lương...) cho các tin đã nhập trước đó
uv run --frozen python -m app.ingestion.cli enrich
```

Search theo nghĩa (dense hoặc hybrid) cần tải model và tạo index:

```bash
uv run --frozen python -m app.retrieval.cli download-model
uv run --frozen python -m app.retrieval.cli index
```

## Ví dụ API

```bash
# Tìm việc
curl --json '{"query":"backend Python","top_k":5}' localhost:8000/search

# Hỏi dữ liệu
curl --json '{"question":"Có bao nhiêu tin nhắc đến Python?"}' localhost:8000/ask

# Upload CV: kết quả trả về access_token để gọi tiếp /cv/{id}/matches
curl -H 'Content-Type: application/pdf' --data-binary @my-cv.pdf localhost:8000/cv
```

## Phát triển

```bash
uv run --frozen ruff check . && uv run --frozen mypy
uv run --frozen pytest -m 'not integration'
RUN_INTEGRATION=1 uv run --frozen pytest -m integration   # cần stack Docker đang chạy
```

Integration test chỉ ghi vào schema tạm, nhưng đừng chạy với database production. CI chạy toàn bộ các bước trên mà không cần API key.

## Lưu ý quan trọng

- **Dữ liệu chỉ nằm trên máy local**, không có trong Git. Snapshot ngày 2026-11-10 có 838 tin, chủ yếu từ JobTech, nên chưa đại diện cho thị trường Việt Nam.
- **ITviec** chỉ dùng cho nghiên cứu cá nhân. Nguồn này bị ẩn khi chạy ngoài môi trường local và không bao giờ gửi tới model bên ngoài.
- **Chất lượng search** mới được đo trên 60 câu hỏi với nhãn tự động (silver), chưa có người duyệt. Xem [M2_VALIDATION](docs/M2_VALIDATION.md).
- **Kỹ năng** là những gì được nhắc trong tin. Kỹ năng không xuất hiện trong CV không có nghĩa là ứng viên không biết. Điểm match chỉ là heuristic, không phải xác suất được tuyển.
- Các con số trong mô tả dự án ban đầu (1.081 jobs, 323 tests...) chỉ là mốc tham chiếu, **không phải** kết quả của repo này. Chỉ đưa thành tích vào CV khi có dữ liệu và artifact chứng minh.

## Tài liệu

- [Kế hoạch và milestones](docs/IMPLEMENTATION_PLAN.md) · [Cài đặt local](docs/DEVELOPMENT.md)
- Nạp dữ liệu: [INGESTION](docs/INGESTION.md) · [Nguồn Việt Nam](docs/VIETNAM_SOURCES.md) · [Bổ sung dữ liệu](docs/DATA_REFRESH.md)
- Tìm kiếm: [RETRIEVAL](docs/RETRIEVAL.md) · [Reranker, retry, quota](docs/RELIABILITY.md)
- Hỏi dữ liệu: [ANALYTICS](docs/ANALYTICS.md) · [Qwen LAN](docs/QWEN_LAN_VALIDATION.md)
- CV: [CV_MATCHING](docs/CV_MATCHING.md) · Giao diện: [PLATFORM_UI](docs/PLATFORM_UI.md)
- Vận hành: [OPERATIONS](docs/OPERATIONS.md)
- Kết quả kiểm chứng: [M1](docs/M1_VALIDATION.md) · [M2](docs/M2_VALIDATION.md) · [M3](docs/M3_VALIDATION.md) · [M4](docs/M4_VALIDATION.md) · [M5](docs/M5_VALIDATION.md) · [M6](docs/M6_VALIDATION.md)
