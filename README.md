# AI Career Intelligence Platform

Nền tảng hỗ trợ tìm việc IT bằng ngôn ngữ tự nhiên, phân tích thị trường tuyển dụng và đối chiếu CV với công việc, tập trung vào Việt Nam và châu Âu.

## Trạng thái

Đã khởi tạo repository và kế hoạch. **Chưa triển khai ứng dụng, thu thập dữ liệu hay chạy đánh giá.** Các con số trong mô tả dự án ban đầu (1.081 jobs, 323 tests, 12/12 routing, 5/5 SQL, P@5 0,34/0,88) là mốc tham chiếu cần kiểm chứng, không phải kết quả của repository này.

## Phạm vi dự kiến

- Tìm việc: BM25 + dense embeddings + Reciprocal Rank Fusion, sau đó LLM reranking có kiểm tra đầu ra.
- Phân tích: LangGraph điều phối search hoặc text-to-SQL với giới hạn và phân quyền ở database.
- CV matching: đọc PDF có text, trích xuất kỹ năng kèm bằng chứng và báo cáo khoảng trống kỹ năng.
- Vận hành: FastAPI, PostgreSQL, Qdrant, Docker Compose, CI và demo có kiểm soát chi phí.

Stack dự kiến: Python, FastAPI, Pydantic, SQLAlchemy/Alembic, LangChain, LangGraph, PostgreSQL, Qdrant và DeepSeek cho tác vụ sinh văn bản. Provider embeddings sẽ được chọn và kiểm tra riêng; không giả định API chat có hỗ trợ embeddings.

## Tài liệu

- [Kế hoạch triển khai, milestones và điều kiện nghiệm thu](docs/IMPLEMENTATION_PLAN.md)

Chỉ thêm thành tích vào README/CV sau khi có dữ liệu, lệnh chạy và artifact chứng minh.
