# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT bằng ngôn ngữ tự nhiên, phân tích thị trường tuyển dụng và đối chiếu CV với công việc, tập trung vào Việt Nam và châu Âu.

## Trạng thái

M0 đã hoàn thành. M1 đã có importer idempotent, lịch sử nguồn, curated skill catalog, extraction kèm evidence, alias review, quota DeepSeek và API đọc jobs. Dataset kiểm chứng local: 145 tin Data/IT từ API JobTech (CC0); xem [báo cáo M1](docs/M1_VALIDATION.md).

Search, embeddings, LangGraph analytics và CV matching chưa triển khai. DeepSeek adapter đã kiểm thử bằng mock, chưa gọi model trả phí để đánh giá chất lượng thực tế.

Các con số trong mô tả dự án ban đầu (1.081 jobs, 323 tests, 12/12 routing, 5/5 SQL, P@5 0,34/0,88) là mốc tham chiếu cần kiểm chứng, không phải kết quả của repository này.

## Chạy local bằng Docker

Yêu cầu: Docker Engine/Desktop đang chạy và Docker Compose v2 trở lên.

```bash
git clone https://github.com/bachthao4321/ai-career-intelligence.git
cd ai-career-intelligence
cp .env.example .env
docker compose up --build --wait --wait-timeout 120
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
```

API docs: <http://localhost:8000/docs>. Compose chạy migration trước API. API, PostgreSQL và Qdrant chỉ publish cổng trên localhost; dữ liệu lưu trong named volumes. Cấu hình này dành cho phát triển local.

`GET /health` trả 200 nếu API hoạt động. `GET /ready` kiểm tra PostgreSQL (`SELECT 1`) và Qdrant (`/readyz`) song song với timeout riêng; trả 200 khi cả hai sẵn sàng, hoặc 503 kèm trạng thái từng dependency. Endpoint này kiểm tra kết nối, không chứng minh dữ liệu/index đã đầy đủ.

```json
{"status":"ready","dependencies":{"postgres":"ok","qdrant":"ok"}}
```

```bash
docker compose logs --tail 100 api migrate
docker compose down
```

Lệnh `down` dừng stack và giữ named volumes. Chạy lại `up` để tiếp tục.

## Phát triển và kiểm thử

Yêu cầu `uv` (phiên bản dùng trong Docker/CI: `0.13.7`). Python 3.12 được chọn trong `.python-version`; `uv` có thể tải interpreter nếu máy chưa có.

```bash
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest -m 'not integration'
```

Chạy integration tests khi stack Docker ở trên đã sẵn sàng và `.env` được tạo:

```bash
RUN_INTEGRATION=1 uv run --frozen pytest -m integration
```

Integration tests dùng PostgreSQL và Qdrant thật; kiểm tra migration upgrade/downgrade, schema drift, persist/constraints và readiness. Các thao tác ghi/downgrade chỉ thực hiện trong schema tạm có tên UUID do test tạo và xóa sau test. Không trỏ bộ test này vào production.

CI chạy lint/format, type checks, unit tests và một job riêng build/chạy toàn bộ Compose stack cùng integration tests, không cần API key hoặc LLM trả phí.

## M1: Nạp dữ liệu và xem kỹ năng

Chạy các lệnh dưới đây trên host sau khi đã tạo `.env`, `uv sync --frozen` và khởi động Compose. Có thể kiểm thử offline bằng dữ liệu giả lập được ghi rõ nhãn:

```bash
uv run --frozen python -m app.ingestion.cli import examples/jobs.synthetic.json
```

Hoặc lấy một mẫu Data/IT thật từ JobTech. Lệnh fetch lưu và tái sử dụng snapshot theo ngày UTC, tối đa 200 tin/lần; API chỉ là nguồn mẫu, không đại diện toàn thị trường:

```bash
uv run --frozen python -m app.ingestion.cli fetch jobtech --limit 150
# Dùng đường dẫn snapshot được lệnh fetch in ra.
uv run --frozen python -m app.ingestion.cli import data/raw/jobtech-YYYY-MM-DD.json
uv run --frozen python -m app.ingestion.cli report --output data/processed/m1-review.json
curl --fail 'http://localhost:8000/jobs?limit=5'
```

`GET /jobs/{job_id}` trả skills cùng evidence, field và offsets. API giữ tên nguồn/link gốc để attribution; không trả raw snapshots. Chạy lại import không tạo trùng cùng `(source, source_job_id)`; nội dung thay đổi được cập nhật và lưu snapshot, extraction được cache theo content/model/rule/catalog version.

Mặc định chỉ dùng matcher curated, không tốn phí LLM. Skills hiện là **các thuật ngữ xuất hiện trong nguồn**, chưa phân biệt bắt buộc, ưu tiên hay phủ định. Không tìm thấy skill không đồng nghĩa công việc không cần skill đó. Xem [hướng dẫn ingestion](docs/INGESTION.md) cho DeepSeek, duyệt alias và giới hạn dữ liệu.

## Phạm vi dự kiến

- Tìm việc: BM25 + dense embeddings + Reciprocal Rank Fusion, sau đó LLM reranking có kiểm tra đầu ra.
- Phân tích: LangGraph điều phối search hoặc text-to-SQL với giới hạn và phân quyền ở database.
- CV matching: đọc PDF có text, trích xuất kỹ năng kèm bằng chứng và báo cáo khoảng trống kỹ năng.
- Vận hành: FastAPI, PostgreSQL, Qdrant, Docker Compose, CI và demo có kiểm soát chi phí.

Stack dự kiến: Python, FastAPI, Pydantic, SQLAlchemy/Alembic, LangChain, LangGraph, PostgreSQL, Qdrant và DeepSeek cho tác vụ sinh văn bản. Provider embeddings sẽ được chọn và kiểm tra riêng; không giả định API chat có hỗ trợ embeddings.

## Tài liệu

- [Kế hoạch triển khai, milestones và điều kiện nghiệm thu](docs/IMPLEMENTATION_PLAN.md)
- [Cấu hình local, migration và troubleshooting](docs/DEVELOPMENT.md)
- [Ingestion, nguồn dữ liệu và alias review](docs/INGESTION.md)
- [Kết quả kiểm chứng M1](docs/M1_VALIDATION.md)

Chỉ thêm thành tích vào README/CV sau khi có dữ liệu, lệnh chạy và artifact chứng minh.
