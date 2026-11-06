import math
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.retrieval.contracts import RankedHit, SearchRequest
from app.retrieval.documents import deduplicate
from app.retrieval.embeddings import EmbeddingUnavailable, LocalMiniLM, validate_vectors
from app.retrieval.evaluation import retrieval_metrics
from app.retrieval.lexical import bm25, reciprocal_rank_fusion, tokenize
from tests.retrieval_helpers import document


def test_unicode_and_technical_tokenization() -> None:
    assert tokenize("Cần tìm C++ C# .NET Node.js CI/CD PostgreSQL") == [
        "c++",
        "c#",
        ".net",
        "node.js",
        "ci/cd",
        "postgresql",
    ]
    assert tokenize("KỸ SƯ dữ liệu") == tokenize("ky su du lieu")
    assert tokenize("åäö") == ["aao"]


def test_bm25_matches_hand_calculated_score_and_excludes_zero_scores() -> None:
    python, java = document("Python"), document("Java", identity=2)
    results = bm25("Python", [python, java])
    assert [hit.job_id for hit in results] == [python.job_id]
    assert results[0].score == pytest.approx(math.log(2))
    assert bm25("nonexistent", [python, java]) == []
    assert bm25("Python", []) == []


def test_rrf_uses_ranks_not_incompatible_raw_scores_and_ignores_duplicate_ids() -> None:
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    rankings = [
        [RankedHit(a, 1000), RankedHit(a, 999), RankedHit(b, 50)],
        [RankedHit(b, 0.9), RankedHit(a, 0.1)],
    ]
    result = reciprocal_rank_fusion(rankings, k=10)
    assert [hit.job_id for hit in result] == [a, b]
    assert result[0].score == pytest.approx(1 / 11 + 1 / 12)
    weighted = reciprocal_rank_fusion(rankings, k=10, weights=[0.5, 1])
    assert weighted[0].job_id == b


@pytest.mark.parametrize("weights", [[-1, 1], [0, 0], [math.nan, 1], [1]])
def test_rrf_rejects_invalid_weights(weights: list[float]) -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[], []], weights=weights)


def test_content_hash_deduplication_retains_one_identity_and_counts() -> None:
    a, b = document("Python"), document("Python", identity=2)
    unique, counts = deduplicate([b, a])
    assert len(unique) == 1
    assert unique[0].job_id == a.job_id
    assert counts[a.content_hash] == 2


@pytest.mark.parametrize(
    "vectors,count,dimension",
    [([[1.0]], 2, 1), ([[1.0]], 1, 2), ([[math.nan]], 1, 1), ([[math.inf]], 1, 1), ([[0.0]], 1, 1)],
)
def test_embedding_validation_rejects_corrupt_results(vectors, count, dimension) -> None:
    with pytest.raises(ValueError):
        validate_vectors(vectors, count, dimension)


def test_missing_model_never_triggers_a_hidden_network_download(tmp_path: Path) -> None:
    model = LocalMiniLM(tmp_path)
    with pytest.raises(EmbeddingUnavailable):
        model.encode(["Python"])
    assert list(tmp_path.iterdir()) == []


def test_chunking_preserves_unicode_offsets_overlap_and_limits(tmp_path: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers

    encoder = LocalMiniLM(tmp_path, max_chunks=2)
    encoder.tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "Python": 1}, unk_token="[UNK]"))
    encoder.tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    encoder._loaded = True
    text = "Python Việt 👩‍💻 " * 200
    chunks, truncated = encoder.chunks(text)
    assert len(chunks) == 2 and truncated
    assert all(chunk.text == text[chunk.start : chunk.end] for chunk in chunks)
    assert chunks[0].start < chunks[1].start < chunks[0].end < chunks[1].end


def test_mean_pooling_ignores_padding_and_normalizes_vectors(tmp_path: Path) -> None:
    from types import SimpleNamespace

    import numpy as np
    from tokenizers import Tokenizer, models, pre_tokenizers

    encoder = LocalMiniLM(tmp_path)
    encoder.tokenizer = Tokenizer(
        models.WordLevel({"[UNK]": 0, "foo": 1, "bar": 3}, unk_token="[UNK]")
    )
    encoder.tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()

    class Session:
        def get_inputs(self):
            return [
                SimpleNamespace(name=name)
                for name in ("input_ids", "attention_mask", "token_type_ids")
            ]

        def run(self, _, feed):
            ids = feed["input_ids"]
            assert ids.dtype == np.int64
            values = np.zeros((*ids.shape, 384), dtype=np.float32)
            values[:, :, 0] = ids
            values[:, :, 1] = 1
            return [values]

    encoder.session = Session()
    encoder._loaded = True
    alone = encoder.encode(["foo"])[0]
    batch = encoder.encode(["foo", "foo bar"])
    assert batch[0] == pytest.approx(alone)
    assert batch[0][0] / batch[0][1] == pytest.approx(1)
    assert batch[1][0] / batch[1][1] == pytest.approx(2)
    assert sum(value * value for value in batch[1]) == pytest.approx(1)


def test_metrics_have_correct_small_relevance_set_ceiling() -> None:
    metrics = retrieval_metrics(["irrelevant", "good"], {"good": 1})
    assert metrics["precision_at_5"] == 0.2
    assert metrics["precision_ceiling_at_5"] == 0.2
    assert metrics["recall_at_5"] == 1
    assert metrics["ndcg_at_5"] == pytest.approx(1 / math.log2(3))
    with pytest.raises(ValueError):
        retrieval_metrics(["good", "good"], {"good": 1})


@pytest.mark.parametrize(
    "extra",
    [
        {"query": "x"},
        {"query": "x" * 501},
        {"query": "Python", "top_k": 21},
        {"query": "Python", "filters": {"source": "x' OR 1=1"}},
        {"query": "Python", "app_env": "local"},
        {"query": "Python", "filters": {"salary": 10}},
    ],
)
def test_search_contract_rejects_unbounded_or_unsupported_input(extra: dict) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(extra)
