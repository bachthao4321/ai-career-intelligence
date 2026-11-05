# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT bằng ngôn ngữ tự nhiên, phân tích thị trường tuyển dụng và đối chiếu CV với công việc, tập trung vào Việt Nam và châu Âu.

## Trạng thái

M0: đã triển khai FastAPI skeleton, health/readiness, PostgreSQL/Qdrant Compose, schema jobs, Alembic migration, dependency lock và CI. Chưa triển khai ingestion, search, LLM, analytics hay CV matching.

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

## Phạm vi dự kiến

- Tìm việc: BM25 + dense embeddings + Reciprocal Rank Fusion, sau đó LLM reranking có kiểm tra đầu ra.
- Phân tích: LangGraph điều phối search hoặc text-to-SQL với giới hạn và phân quyền ở database.
- CV matching: đọc PDF có text, trích xuất kỹ năng kèm bằng chứng và báo cáo khoảng trống kỹ năng.
- Vận hành: FastAPI, PostgreSQL, Qdrant, Docker Compose, CI và demo có kiểm soát chi phí.

Stack dự kiến: Python, FastAPI, Pydantic, SQLAlchemy/Alembic, LangChain, LangGraph, PostgreSQL, Qdrant và DeepSeek cho tác vụ sinh văn bản. Provider embeddings sẽ được chọn và kiểm tra riêng; không giả định API chat có hỗ trợ embeddings.

## Tài liệu

- [Kế hoạch triển khai, milestones và điều kiện nghiệm thu](docs/IMPLEMENTATION_PLAN.md)
- [Cấu hình local, migration và troubleshooting](docs/DEVELOPMENT.md)

Chỉ thêm thành tích vào README/CV sau khi có dữ liệu, lệnh chạy và artifact chứng minh.
