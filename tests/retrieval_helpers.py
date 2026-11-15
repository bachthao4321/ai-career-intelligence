import hashlib
import uuid
from collections.abc import Sequence

from app.integrations.embeddings import TextChunk
from app.schemas.search import SearchDocument


class TestEmbedder:
    __test__ = False
    signature = hashlib.sha256(b"synthetic-test-encoder-v1").hexdigest()
    dimension = 3

    def __init__(self) -> None:
        self.calls = 0

    def chunks(self, text: str) -> tuple[list[TextChunk], bool]:
        return [
            TextChunk(text[start : start + 128], start, min(start + 128, len(text)))
            for start in range(0, len(text), 128)
        ], False

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [
            [
                1 + text.lower().count("python"),
                1 + text.lower().count("java"),
                1 + text.lower().count("docker"),
            ]
            for text in texts
        ]


def document(text: str, *, identity: int = 1, content_hash: str | None = None) -> SearchDocument:
    return SearchDocument(
        job_id=uuid.UUID(int=identity),
        content_hash=content_hash or hashlib.sha256(text.encode()).hexdigest(),
        source="synthetic",
        source_job_id=str(identity),
        source_url=f"https://example.com/jobs/{identity}",
        title="Fixture",
        company=None,
        location=None,
        workplace_type="unknown",
        text=text,
        skills=(),
    )
