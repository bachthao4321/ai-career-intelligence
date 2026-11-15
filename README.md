# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT, tập trung vào Việt Nam và châu Âu:

- **Tìm việc** bằng ngôn ngữ tự nhiên (tiếng Việt, Anh, Thụy Điển).
- **Hỏi số liệu** thị trường tuyển dụng, ví dụ "Top 10 skills".
- **Đối chiếu CV** với công việc và chỉ ra kỹ năng còn thiếu.
- **Lưu việc làm** vào tài khoản để xem lại sau.

Giao diện web **CareerLens** gom tất cả các tính năng trên.

Stack: Python 3.12, FastAPI, PostgreSQL, Qdrant, LangGraph, ONNX MiniLM (chạy local), Docker Compose.

## Tính năng

| Tính năng | Cách hoạt động |
|---|---|
| **Nạp dữ liệu** | Nạp tin từ JobTech, Remotive, ITviec và trang tuyển dụng của các công ty có văn phòng ở Việt Nam. Trích kỹ năng kèm bằng chứng, có phân loại bắt buộc / ưu tiên / phủ định. Trích thêm seniority, số năm kinh nghiệm, lương và hạn nộp. |
| **Tìm kiếm** | Mặc định **hybrid**: kết hợp từ khóa (BM25) với tìm theo nghĩa (embeddings), trọng số 2 : 1. Nếu vector index chưa sẵn sàng thì tự quay về BM25. |
| **Hỏi dữ liệu** (`/ask`) | Định tuyến câu hỏi sang tìm việc, thống kê hoặc yêu cầu hỏi lại cho rõ. Câu hỏi phổ biến chạy theo mẫu có sẵn, không cần model. SQL do model viết phải qua allowlist và chạy bằng tài khoản chỉ đọc. |
| **Đối chiếu CV** | Upload CV dạng PDF có text. Trích kỹ năng, tìm việc phù hợp, chỉ ra kỹ năng job cần mà chưa thấy trong CV. Chạy hoàn toàn local, không lưu file PDF, profile tự hết hạn sau 1 giờ. |
| **Tài khoản** | Đăng ký, đăng nhập bằng email và mật khẩu, lưu tối đa 500 việc làm. Mật khẩu băm bằng scrypt, phiên đăng nhập là cookie HttpOnly. |
| **Vận hành** | Password gate, quota theo client, cấu hình production (Compose + Caddy), backup và restore. Chưa deploy public. |

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
uv run --frozen python -m app.cli ingest import examples/jobs.synthetic.json

# Dữ liệu thật từ JobTech: fetch in ra đường dẫn snapshot để import
uv run --frozen python -m app.cli ingest fetch jobtech --limit 150
uv run --frozen python -m app.cli ingest import data/raw/jobtech-YYYY-MM-DD.json

# Bổ sung thông tin (seniority, lương...) cho các tin đã nhập trước đó
uv run --frozen python -m app.cli ingest enrich
```

Search theo nghĩa (dense hoặc hybrid) cần tải model và tạo index:

```bash
uv run --frozen python -m app.cli search download-model
uv run --frozen python -m app.cli search index
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
- **Chất lượng search** mới được đo trên 60 câu hỏi với nhãn tự động (silver), chưa có người duyệt.
- **Kỹ năng** là những gì được nhắc trong tin. Kỹ năng không xuất hiện trong CV không có nghĩa là ứng viên không biết. Điểm match chỉ là heuristic, không phải xác suất được tuyển.
- Các con số trong mô tả dự án ban đầu (1.081 jobs, 323 tests...) chỉ là mốc tham chiếu, **không phải** kết quả của repo này. Chỉ đưa thành tích vào CV khi có dữ liệu và artifact chứng minh.
