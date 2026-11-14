# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT bằng ngôn ngữ tự nhiên, phân tích thị trường tuyển dụng và đối chiếu CV với công việc, tập trung vào Việt Nam và châu Âu.

## Trạng thái

M0 đã hoàn thành. M1 đã có importer idempotent, lịch sử nguồn, curated skill catalog, extraction kèm evidence, alias review, quota DeepSeek và API đọc jobs. Snapshot local ngày 2026-11-10 có **838 tin / 810 nhóm nội dung**: 821 JobTech, 10 ITviec nghiên cứu cá nhân local và 7 Remotive. Xem [lần bổ sung dữ liệu](docs/DATA_REFRESH.md), [baseline M1](docs/M1_VALIDATION.md) và [nguồn Việt Nam](docs/VIETNAM_SOURCES.md). Đây là dữ liệu local không nằm trong Git; checkout mới không tự có các tin này.

M2 đã có search BM25/dense/hybrid, embeddings ONNX đa ngôn ngữ chạy local, Qdrant indexing incremental/resumable và pilot 60 query. **Hybrid là mặc định** (RRF, trọng số BM25 2 : dense 1) từ lần đánh giá lại ngày 2026-11-14 trên corpus JobTech hiện tại; tự quay về BM25 khi vector index chưa sẵn sàng. Nhãn vẫn là silver, chưa có người duyệt; xem [kết quả M2](docs/M2_VALIDATION.md#re-evaluation--2026-11-14).

M3 đã có `/ask`, router LangGraph, các mẫu thống kê offline, adapter chat tùy chọn, AST SQL allowlist và PostgreSQL login riêng chỉ đọc scoped views. Xem [hướng dẫn M3](docs/ANALYTICS.md) và [kiểm chứng M3](docs/M3_VALIDATION.md). Đã nối Qwen LAN và chạy smoke thực tế cho routing/text-to-SQL; đây chưa phải benchmark held-out. Xem [kiểm chứng Qwen](docs/QWEN_LAN_VALIDATION.md).

M4 đã có CV PDF upload, trích kỹ năng có bằng chứng theo trang, BM25 + skill-coverage matching và báo cáo kỹ năng chưa thấy trong CV. Profile dùng token riêng, TTL mặc định 1 giờ và API xóa; PDF gốc không được lưu. Toàn bộ xử lý CV chạy local, không gọi LLM/embeddings hoặc gửi CV tới Qdrant. Xem [hướng dẫn CV](docs/CV_MATCHING.md) và [kiểm chứng M4](docs/M4_VALIDATION.md).

M5 có bounded reranker tùy chọn, public-job batch extraction, retry/split, cache và quota lưu bền, cùng workflow pool review → chọn dev → frozen held-out test. Mặc định không gọi model. Chưa có nhãn relevance người duyệt hoặc đánh giá chat model thật; xem [hướng dẫn M5](docs/RELIABILITY.md) và [kiểm chứng M5](docs/M5_VALIDATION.md).

M6 có password gate, signed demo cookie tách CV bearer, admin-key writes, client quotas lưu DB, production Compose/Caddy và backup/restore drill PostgreSQL/Qdrant. Đã kiểm thử HTTPS trên localhost và phục hồi dữ liệu thật sang volumes riêng; chưa deploy public do chưa có domain/VPS. Xem [runbook M6](docs/OPERATIONS.md) và [kết quả local](docs/M6_VALIDATION.md).

Các con số trong mô tả dự án ban đầu (1.081 jobs, 323 tests, 12/12 routing, 5/5 SQL, P@5 0,34/0,88) là mốc tham chiếu cần kiểm chứng, không phải kết quả của repository này.

## Chạy local bằng Docker

Giao diện **CareerLens** có tại <http://localhost:8000/> sau khi khởi động stack: khám phá việc làm, hỏi dữ liệu và đối chiếu CV. UI dùng API/dataset hiện có, không cần cài thêm frontend dependencies. Xem [hướng dẫn giao diện](docs/PLATFORM_UI.md).

Yêu cầu: Docker Engine/Desktop đang chạy và Docker Compose v2 trở lên.

```bash
git clone https://github.com/bachthao4321/ai-career-intelligence.git
cd ai-career-intelligence
cp .env.example .env
docker compose up --build --wait --wait-timeout 120
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
```

API docs: <http://localhost:8000/docs>. Compose chạy migration và provision role analytics riêng trước API. API, PostgreSQL và Qdrant chỉ publish cổng trên localhost; dữ liệu lưu trong named volumes. Cấu hình này dành cho phát triển local.

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

Lấy mẫu nhỏ ITviec để thử nghiệm cá nhân local:

```bash
uv run --frozen python -m app.ingestion.cli fetch itviec --limit 10
uv run --frozen python -m app.ingestion.cli import data/raw/itviec-YYYY-MM-DD.json
```

Adapter chỉ đọc trang công khai/JSON-LD, kiểm tra robots.txt, chờ ít nhất 2 giây giữa request và dừng khi gặp 401/403/429. Mẫu mặc định 10, tối đa 20 tin, cache theo ngày. Đây **không phải nguồn CC0**: dữ liệu gốc không được commit; API gắn `usage_scope=local_research_only`, ẩn nguồn này khi `APP_ENV` khác `local` và importer không gửi nó tới DeepSeek. Cần xác minh quyền phù hợp trước khi dùng cho public demo. Chưa có fetcher TopCV.

## M2: Tìm kiếm

Sau khi nạp jobs, BM25 dùng được ngay mà không cần tải model. Dense/hybrid cần model local và bước indexing:

```bash
uv run --frozen python -m app.retrieval.cli download-model
uv run --frozen python -m app.retrieval.cli index
uv run --frozen python -m app.retrieval.cli search "lập trình viên Python" --mode hybrid
```

Model ONNX được khóa revision, khoảng 224 MiB cho weights, tạo vector 384 chiều. Lệnh download chỉ tải model; API không tự tải model và không gửi nội dung jobs tới nhà cung cấp embeddings. Docker mount `./models` ở chế độ chỉ đọc. Chạy lại index bỏ qua jobs đã có đúng fingerprint/point IDs; dùng `index --max-jobs 5` để thực hiện một lượt giới hạn, rồi `index` để tiếp tục.

```bash
curl --fail http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"backend Python","mode":"hybrid","top_k":5,"filters":{"skills":["python"]}}'
```

API `POST /search` có ba chế độ `bm25`, `dense`, `hybrid`. Filters gồm `source`, `location` (chuỗi con literal), `workplace_type` và `skills` (tất cả slugs được yêu cầu). Không tự suy ra filter cứng từ câu hỏi. Kết quả được gộp theo content hash sau khi lọc phạm vi/metadata; có điểm thành phần, source URL, excerpt và thông báo fallback. Score không phải phần trăm phù hợp công việc.

Hybrid fallback sang BM25 khi model/index/Qdrant chưa sẵn sàng và ghi rõ `effective_mode`/`warnings`; yêu cầu dense riêng trả 503 khi không chạy được. BM25 đọc snapshot DB mới mỗi lần tìm kiếm. Sau khi thêm/sửa/xóa jobs, chạy lại index để cập nhật dense; vector cũ không khớp fingerprint bị loại khỏi truy vấn.

Các collection local/public và model version tách biệt. ITviec chỉ được xử lý bằng model local và Qdrant endpoint local; search public loại nguồn này trước cả thống kê BM25 lẫn dense candidates. Xem [hướng dẫn retrieval](docs/RETRIEVAL.md) cho cấu hình, giới hạn và tái lập pilot.

## M4: CV PDF và báo cáo matching

Gửi PDF có text bằng raw body `application/pdf` (tối đa 2 MiB, 10 trang). API docs hỗ trợ chọn file tại `POST /cv`:

```bash
curl --fail http://localhost:8000/cv \
  -H 'Content-Type: application/pdf' --data-binary @my-cv.pdf
```

Kết quả gồm `id`, `access_token`, `expires_at`, văn bản theo trang và các skill/evidence. Giữ token ở client; các lệnh sau yêu cầu header `Authorization: Bearer <access_token>`:

- `GET /cv/{id}`: xem văn bản/kỹ năng đã trích.
- `POST /cv/{id}/matches`: gửi `{"top_k":5,"target_role":"backend","filters":{"location":"Hồ Chí Minh"}}` để tìm và so sánh jobs. `target_role` và filters đều tùy chọn.
- `DELETE /cv/{id}`: xóa profile trong database đang chạy.

Match trả source URL, kỹ năng trùng kèm bằng chứng cả CV/job và `skills_not_seen_in_cv` kèm bằng chứng job. Cụm này nghĩa là chưa tìm thấy trong văn bản CV đã trích, không kết luận ứng viên không biết hoặc job bắt buộc kỹ năng đó. Score là heuristic chưa hiệu chỉnh, không phải xác suất được tuyển. PDF scan không có text/OCR, PDF mã hóa hoặc vượt giới hạn sẽ bị từ chối có mã lỗi.

Profile hết hạn bị chặn đọc/match ngay; background sweep xóa các hàng hết hạn mỗi 60 giây khi API/DB hoạt động. Có thể chạy `uv run --frozen python -m app.cv.cli cleanup` để purge thủ công. Xóa/TTL áp dụng tới database hiện hành; bản sao lưu cần chính sách lưu giữ riêng.

## Phạm vi dự kiến

- Tìm việc: BM25 + dense embeddings + Reciprocal Rank Fusion, sau đó LLM reranking có kiểm tra đầu ra.
- Phân tích: LangGraph điều phối search hoặc text-to-SQL với giới hạn và phân quyền ở database.
- CV matching: đọc PDF có text, trích xuất kỹ năng kèm bằng chứng và báo cáo khoảng trống kỹ năng.
- Vận hành: FastAPI, PostgreSQL, Qdrant, Docker Compose, CI và demo có kiểm soát chi phí.

Stack hiện tại: Python, FastAPI, Pydantic, SQLAlchemy/Alembic, LangGraph, SQLGlot, PostgreSQL, Qdrant và ONNX MiniLM local. LangChain Core là dependency của LangGraph; chat adapter dùng HTTP theo giao thức tương thích OpenAI, tách biệt embeddings.

## M3: Hỏi dữ liệu tuyển dụng

Sau khi chạy Compose mới, các câu phổ biến chạy được mà không có API key:

```bash
curl --fail --json '{"question":"Có bao nhiêu tin nhắc đến Python?"}' http://localhost:8000/ask
curl --fail --json '{"question":"Top 10 skills"}' http://localhost:8000/ask
curl --fail --json '{"question":"Tìm việc backend Python"}' http://localhost:8000/ask
```

Luồng LangGraph chọn search, analytics hoặc yêu cầu làm rõ. Mẫu offline dùng full match; câu có thêm điều kiện không bị cắt bỏ để khớp mẫu. Ngoài các mẫu này, adapter model mặc định tắt nên API yêu cầu làm rõ. Có thể bật model chat local/hosted qua cấu hình trong [ANALYTICS.md](docs/ANALYTICS.md); đây chưa phải chatbot đã được đánh giá trên câu hỏi tự do.

SQL model sinh ra luôn được kiểm tra AST và viết lại tới view đúng scope trước khi chạy. Chỉ hỗ trợ SELECT phẳng, COUNT, group/filter/order và join khóa jobs–skills; tối đa 50 dòng, timeout mặc định 1,5 giây. Analytics dùng login riêng, không có quyền đọc bảng gốc hay ghi view. Kết quả trả bảng và SQL thực thi, không gửi dữ liệu hoặc kết quả SQL tới model để diễn giải. Số đếm mặc định là tin theo nguồn; `COUNT(DISTINCT content_hash)` dùng để đếm nhóm nội dung trùng.

## Tài liệu

- [Kế hoạch triển khai, milestones và điều kiện nghiệm thu](docs/IMPLEMENTATION_PLAN.md)
- [Cấu hình local, migration và troubleshooting](docs/DEVELOPMENT.md)
- [Ingestion, nguồn dữ liệu và alias review](docs/INGESTION.md)
- [Kết quả kiểm chứng M1](docs/M1_VALIDATION.md)
- [ITviec: phạm vi dữ liệu và kiểm chứng](docs/VIETNAM_SOURCES.md)
- [Retrieval/indexing và chạy pilot](docs/RETRIEVAL.md)
- [Kết quả và giới hạn M2](docs/M2_VALIDATION.md)
- [Router, SQL guard, role và model tùy chọn](docs/ANALYTICS.md)
- [Kết quả kiểm chứng M3](docs/M3_VALIDATION.md)
- [CV upload, bằng chứng, matching và retention](docs/CV_MATCHING.md)
- [Kết quả kiểm chứng M4](docs/M4_VALIDATION.md)
- [Reranker, batching, retry/cache/quota và held-out evaluation](docs/RELIABILITY.md)
- [Kết quả kiểm chứng và phần còn lại M5](docs/M5_VALIDATION.md)
- [Xác thực, production deployment và backup/restore](docs/OPERATIONS.md)
- [Kết quả kiểm chứng local M6](docs/M6_VALIDATION.md)

Chỉ thêm thành tích vào README/CV sau khi có dữ liệu, lệnh chạy và artifact chứng minh.
