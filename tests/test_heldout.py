import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.ingestion.contracts import json_hash
from app.retrieval import heldout
from app.retrieval.contracts import RerankInfo
from tests.retrieval_helpers import TestEmbedder, document


def fixture_packet():
    queries = [
        {"id": split, "family": split, "split": split, "language": "en", "query": "Python roles"}
        for split in ["dev", "test"]
    ]
    packet = {
        "queries": queries,
        "query_spec_sha256": heldout.query_signature(queries),
        "label_origin": "human_relevance",
        "review": {
            "reviewer": "synthetic-test-reviewer",
            "completed_at": "fixture-only",
            "statement": heldout.REVIEW_STATEMENT,
        },
        "pool": {split: ["a", "b"] for split in ["dev", "test"]},
        "relevance": {split: {"a": 3, "b": 0} for split in ["dev", "test"]},
        "documents": {"a": {}, "b": {}},
    }
    packet["packet_sha256"] = heldout.packet_signature(packet)
    return packet


@pytest.mark.parametrize(
    "mutation", ["unreviewed", "missing", "bool", "family", "query", "document"]
)
def test_review_guards_missing_labels_provenance_and_packet_changes(mutation: str) -> None:
    packet = fixture_packet()
    heldout.validate_review(packet)
    if mutation == "unreviewed":
        packet["label_origin"] = "unreviewed"
    elif mutation == "missing":
        packet["relevance"]["dev"]["a"] = None
    elif mutation == "bool":
        packet["relevance"]["dev"]["a"] = True
    elif mutation == "family":
        packet["queries"][0]["family"] = "test"
    elif mutation == "query":
        packet["queries"][0]["query"] = "different query"
    else:
        packet["documents"]["a"]["text"] = "different evidence"
    with pytest.raises(ValueError):
        heldout.validate_review(packet)


def test_unknown_results_are_not_silently_scored_as_irrelevant() -> None:
    with pytest.raises(ValueError, match="Unjudged"):
        heldout.judged_metrics(["unjudged"], {"a": 3})
    metrics = heldout.judged_metrics(["a", "b"], {"a": 3, "b": 0})
    assert metrics["precision_at_5"] == 0.2
    assert metrics["pooled_recall_at_5"] == 1


def test_artifacts_cannot_escape_or_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        heldout.save(Path("outside.json"), {})
    path = Path("data/evaluations/fixture.json")
    heldout.save(path, {"fixture": True})
    with pytest.raises(FileExistsError):
        heldout.save(path, {})


async def test_dev_selection_freezes_config_and_test_never_tunes(
    tmp_path: Path,
    monkeypatch,
    settings: Settings,
) -> None:
    monkeypatch.chdir(tmp_path)
    docs = [document("Python", identity=n) for n in range(1, 7)]
    requests = []

    class Service:
        reranker = None
        embedder = TestEmbedder()

        async def search(self, request, **kwargs):
            requests.append((request.query, request.mode, kwargs["rrf_k"]))
            return SimpleNamespace(
                effective_mode=request.mode,
                indexed_documents=len(docs),
                items=[SimpleNamespace(content_hash=doc.content_hash) for doc in docs[:5]],
                latency_ms=1,
                rerank=RerankInfo(status="disabled") if request.rerank else RerankInfo(),
            )

    # Distinct documents are necessary for identity-based metric validation.
    docs = [document(f"Python {n}", identity=n) for n in range(1, 7)]
    service = Service()
    service.settings = settings

    async def corpus(_):
        return docs

    monkeypatch.setattr(heldout, "checked_corpus", corpus)
    monkeypatch.setattr(heldout, "code_signature", lambda: "fixture-code")
    queries = [
        {"id": split, "family": split, "split": split, "language": "en", "query": f"Python {split}"}
        for split in ["dev", "test"]
    ]
    query_path = Path("queries.json")
    await asyncio.to_thread(query_path.write_text, json.dumps(queries))
    dataset = Path("data/evaluations/review.json")
    prepared = await heldout.prepare(service, query_path, dataset)
    assert prepared["label_origin"] == "unreviewed" and prepared["judgments_needed"] == 10
    selection = Path("data/evaluations/dev.json")
    with pytest.raises(ValueError, match="Human review"):
        await heldout.evaluate_heldout(service, dataset, selection, split="dev")
    data = heldout.load(dataset)
    data["label_origin"] = "human_relevance"
    data["review"] = fixture_packet()["review"]
    for labels in data["relevance"].values():
        for identity in labels:
            labels[identity] = 2 if identity == docs[0].content_hash else 0
    # Only a synthetic fixture is assigned test labels here; real review is never automated.
    reviewed = Path("data/evaluations/reviewed-fixture.json")
    heldout.save(reviewed, data)
    requests.clear()
    dev = await heldout.evaluate_heldout(service, reviewed, selection, split="dev")
    assert all("dev" in query for query, _, _ in requests)
    assert dev["selected_rrf"] == heldout.CONFIGS[0]
    assert dev["model_usage"]["monetary_cost"] is None
    requests.clear()
    test_output = Path("data/evaluations/test.json")
    with pytest.raises(ValueError, match="requires"):
        await heldout.evaluate_heldout(service, reviewed, test_output, split="test")
    test = await heldout.evaluate_heldout(
        service, reviewed, test_output, split="test", selection=selection
    )
    assert len(requests) == 4 and all("test" in query for query, _, _ in requests)
    assert {k for _, _, k in requests} == {dev["selected_rrf"][0]}
    assert test["selection_sha256"] == json_hash(heldout.load(selection))
    assert test["summary"]["hybrid_rerank"]["rerank_fallback_or_disabled"] == 1
    service.settings = settings.model_copy(update={"rerank_candidates": 3})
    with pytest.raises(ValueError, match="does not match"):
        await heldout.evaluate_heldout(
            service,
            reviewed,
            Path("data/evaluations/changed.json"),
            split="test",
            selection=selection,
        )
    corpus_reads = 0

    async def changing_corpus(_):
        nonlocal corpus_reads
        corpus_reads += 1
        return docs if corpus_reads == 1 else [document("changed", identity=99), *docs[1:]]

    monkeypatch.setattr(heldout, "checked_corpus", changing_corpus)
    inconsistent = Path("data/evaluations/inconsistent-pool.json")
    with pytest.raises(ValueError, match="Corpus changed during pooling"):
        await heldout.prepare(service, query_path, inconsistent)
    assert not await asyncio.to_thread(inconsistent.exists)
    monkeypatch.setattr(heldout, "checked_corpus", corpus)
    service.settings = settings
    altered = copy.deepcopy(data)
    altered["relevance"]["test"][docs[0].content_hash] = 0
    changed_dataset = Path("data/evaluations/changed-labels.json")
    heldout.save(changed_dataset, altered)
    with pytest.raises(ValueError, match="does not match"):
        await heldout.evaluate_heldout(
            service,
            changed_dataset,
            Path("data/evaluations/new.json"),
            split="test",
            selection=selection,
        )
