"""Separate M5 review pool, dev selection, and frozen held-out evaluation.

Missing judgments are never silently treated as irrelevant. Labels are supplied by a
human reviewer; preparation only creates empty judgment slots over public JobTech jobs.
"""

import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.ingestion.contracts import json_hash
from app.llm.runtime import CallBudget
from app.retrieval.contracts import SearchDocument, SearchFilters, SearchRequest
from app.retrieval.documents import deduplicate, load_documents
from app.retrieval.evaluation import aggregate, corpus_fingerprint, retrieval_metrics
from app.retrieval.service import SearchService

CONFIGS = [(60, 1.0, 1.0), (20, 1.0, 1.0), (100, 1.0, 1.0), (60, 0.5, 1.0)]
REVIEW_STATEMENT = "I personally reviewed every relevance judgment in this dataset."


def artifact_path(path: Path) -> None:
    if not path.resolve().is_relative_to(Path("data/evaluations").resolve()):
        raise ValueError("M5 artifacts must be under ignored data/evaluations/")


def save(path: Path, value: dict[str, Any]) -> None:
    artifact_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A new name is required for each review/selection/run; no overwriting test evidence.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def load(path: Path) -> dict[str, Any]:
    artifact_path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("M5 artifact exceeds 20 MB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected an artifact object")
    return value


def validate_queries(queries: list[dict[str, Any]]) -> None:
    if not 2 <= len(queries) <= 100:
        raise ValueError("Expected 2..100 authored queries")
    ids, families = set(), defaultdict(set)
    for query in queries:
        if not isinstance(query["id"], str) or query["id"] in ids:
            raise ValueError("Query ids must be unique strings")
        ids.add(query["id"])
        if query["split"] not in {"dev", "test"}:
            raise ValueError("Unknown split")
        SearchRequest(query=query["query"])
        families[query["split"]].add(query["family"])
    if not families["dev"] or not families["test"] or families["dev"] & families["test"]:
        raise ValueError("Dev/test must both exist and have disjoint intent families")


def validate_review(data: dict[str, Any]) -> None:
    validate_queries(data["queries"])
    review = data.get("review", {})
    if (
        data.get("label_origin") != "human_relevance"
        or review.get("statement") != REVIEW_STATEMENT
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
        or not isinstance(review.get("completed_at"), str)
        or not review["completed_at"].strip()
    ):
        raise ValueError("Human review and explicit reviewer attestation are required")
    if data["query_spec_sha256"] != query_signature(data["queries"]):
        raise ValueError("Queries changed after candidate pooling; prepare a new pool")
    if data["packet_sha256"] != packet_signature(data):
        raise ValueError("Review packet content changed; prepare a new pool")
    if set(data["relevance"]) != {query["id"] for query in data["queries"]}:
        raise ValueError("Judgments must cover exactly the query ids")
    for query in data["queries"]:
        labels = data["relevance"][query["id"]]
        if not labels or set(labels) != set(data["pool"][query["id"]]):
            raise ValueError("Every pooled candidate requires a judgment")
        if any(type(grade) is not int or not 0 <= grade <= 3 for grade in labels.values()):
            raise ValueError("Missing/invalid judgments: supply integer grades 0..3")
        if any(key not in data["documents"] for key in labels):
            raise ValueError("Judgment points outside the frozen corpus")


def query_signature(queries: list[dict[str, Any]]) -> str:
    return json_hash(queries)


def packet_signature(data: dict[str, Any]) -> str:
    return json_hash(
        {
            key: value
            for key, value in data.items()
            if key not in {"packet_sha256", "label_origin", "review", "relevance"}
        }
    )


def code_signature() -> str:
    paths = sorted(Path("app").rglob("*.py")) + [Path("uv.lock")]
    return json_hash([(str(path), hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths])


def environment_signature(service: SearchService) -> str:
    settings = service.settings
    return json_hash(
        {
            "code": code_signature(),
            "embedding": service.embedder.signature,
            "runtime": service.reranker.runtime.signature if service.reranker else None,
            "reranker": [
                settings.rerank_enabled,
                settings.rerank_candidates,
                settings.rerank_timeout_seconds,
            ],
            "limits": [
                settings.llm_daily_call_limit,
                settings.llm_global_daily_call_limit,
                settings.llm_timeout_seconds,
                settings.llm_max_attempts,
                settings.llm_min_interval_seconds,
                settings.llm_concurrency,
            ],
        }
    )


async def checked_corpus(service: SearchService) -> list[SearchDocument]:
    documents, _ = deduplicate(
        await load_documents(service.engine, service.settings, SearchFilters(source="jobtech"))
    )
    if not documents or any(doc.source != "jobtech" or doc.private for doc in documents):
        raise ValueError("Held-out evaluation requires a public JobTech-only corpus")
    return documents


async def prepare(service: SearchService, queries_path: Path, output: Path) -> dict[str, Any]:
    queries = json.loads(await asyncio.to_thread(queries_path.read_text, encoding="utf-8"))
    validate_queries(queries)
    documents = await checked_corpus(service)
    pool: dict[str, list[str]] = {}
    for query in queries:
        candidates: set[str] = set()
        for mode, config in [
            ("bm25", CONFIGS[0]),
            ("dense", CONFIGS[0]),
            *(("hybrid", config) for config in CONFIGS),
        ]:
            response = await service.search(
                SearchRequest(
                    query=query["query"],
                    mode=mode,
                    top_k=20,
                    filters=SearchFilters(source="jobtech"),
                ),
                rrf_k=config[0],
                rrf_weights=(config[1], config[2]),
            )
            if response.effective_mode != mode or (
                mode != "bm25" and response.indexed_documents != len(documents)
            ):
                raise ValueError("Pooling requires a complete, non-degraded dense index")
            candidates.update(hit.content_hash for hit in response.items)
        if len(candidates) < 5:
            raise ValueError("Too few candidates for a P@5 review pool")
        # Sorted hashes hide retrieval ordering from reviewers.
        pool[query["id"]] = sorted(candidates)
    data = {
        "version": "m5-human-review-v1",
        "label_origin": "unreviewed",
        "review": {"reviewer": None, "completed_at": None, "statement": None},
        "query_spec_sha256": query_signature(queries),
        "corpus_fingerprint": corpus_fingerprint(documents),
        "pooling_embedding": service.embedder.signature,
        "queries": queries,
        "pool": pool,
        "documents": {
            doc.content_hash: {
                "title": doc.title,
                "text": doc.text,
                "source_url": doc.source_url,
                "location": doc.location,
                "workplace_type": doc.workplace_type,
            }
            for doc in documents
        },
        "relevance": {key: {identity: None for identity in values} for key, values in pool.items()},
        "limitations": [
            "Authored pilot queries; convenience JobTech corpus, not market-representative.",
            "Recall is measured only against judged pooled candidates.",
            "Review labels 0=irrelevant, 1=partial, 2=relevant, 3=strongly relevant.",
            "Descriptions may be truncated to the search document's 20000-character cap.",
        ],
    }
    data["packet_sha256"] = packet_signature(data)
    if corpus_fingerprint(await checked_corpus(service)) != data["corpus_fingerprint"]:
        raise ValueError("Corpus changed during pooling; discard this packet")
    await asyncio.to_thread(save, output, data)
    return {
        "output": str(output),
        "label_origin": "unreviewed",
        "queries": len(queries),
        "unique_documents": len(documents),
        "judgments_needed": sum(map(len, pool.values())),
    }


def judged_metrics(ranked: list[str], labels: dict[str, int]) -> dict[str, float]:
    if any(identity not in labels for identity in ranked[:5]):
        raise ValueError("Unjudged output: extend and re-review the pool before evaluation")
    metrics = retrieval_metrics(ranked, labels)
    metrics["pooled_recall_at_5"] = metrics.pop("recall_at_5")
    return metrics


def budget_summary(budget: CallBudget) -> dict[str, Any]:
    return {
        "provider_calls": budget.dispatched,
        "cache_hits": budget.cache_hits,
        "attempts_with_usage": budget.attempts_with_usage,
        "reported_tokens": budget.tokens,
        "attempts_without_usage": budget.dispatched - budget.attempts_with_usage,
        "monetary_cost": None,
        "cost_note": "No verified pricing supplied; unknown is not zero.",
    }


async def run_rows(
    service: SearchService,
    data: dict[str, Any],
    split: str,
    config: tuple[int, float, float],
    budget: CallBudget,
    *,
    ablation: bool = True,
) -> list[dict[str, Any]]:
    rows = []
    modes = (
        [("bm25", False), ("dense", False), ("hybrid", False), ("hybrid", True)]
        if ablation
        else [("hybrid", False)]
    )
    for query in data["queries"]:
        if query["split"] != split:
            continue
        for mode, rerank in modes:
            response = await service.search(
                SearchRequest(
                    query=query["query"],
                    mode=mode,
                    rerank=rerank,
                    top_k=5,
                    filters=SearchFilters(source="jobtech"),
                ),
                rrf_k=config[0],
                rrf_weights=(config[1], config[2]),
                call_budget=budget,
            )
            if response.effective_mode != mode or (
                mode != "bm25" and response.indexed_documents != len(data["documents"])
            ):
                raise ValueError("Evaluation refuses incomplete/degraded retrieval")
            hashes = [hit.content_hash for hit in response.items]
            rows.append(
                {
                    "id": query["id"],
                    "split": split,
                    "mode": "hybrid_rerank" if rerank else mode,
                    "language": query["language"],
                    "latency_ms": response.latency_ms,
                    "metrics": judged_metrics(hashes, data["relevance"][query["id"]]),
                    "results": hashes,
                    "rerank": response.rerank.model_dump(mode="json"),
                }
            )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["mode"]].append(row)
        groups[row["mode"] + "/" + row["language"]].append(row)
    return {
        key: {
            **aggregate(group),
            "rerank_applied": sum(row["rerank"]["status"] == "applied" for row in group),
            "rerank_fallback_or_disabled": sum(
                row["mode"] == "hybrid_rerank" and row["rerank"]["status"] != "applied"
                for row in group
            ),
        }
        for key, group in groups.items()
    }


async def evaluate_heldout(
    service: SearchService,
    dataset: Path,
    output: Path,
    *,
    split: str,
    selection: Path | None = None,
    max_calls: int = 0,
) -> dict[str, Any]:
    if split not in {"dev", "test"} or not 0 <= max_calls <= 100:
        raise ValueError("Invalid split or call budget")
    artifact_path(output)
    if await asyncio.to_thread(output.exists):
        raise ValueError("Use a fresh output path")
    data = await asyncio.to_thread(load, dataset)
    validate_review(data)
    if service.settings.rerank_enabled and service.settings.llm_model_revision == "unverified":
        raise ValueError("Pin an actual model revision before evaluating enabled reranking")
    docs = await checked_corpus(service)
    if corpus_fingerprint(docs) != data["corpus_fingerprint"]:
        raise ValueError("Frozen evaluation corpus changed")
    environment = await asyncio.to_thread(environment_signature, service)
    dataset_hash = json_hash(data)
    budget = CallBudget(max_calls)
    tuning: list[dict[str, Any]] = []
    chosen: tuple[int, float, float]
    if split == "dev":
        for config in CONFIGS:
            trial = await run_rows(service, data, "dev", config, budget, ablation=False)
            tuning.append({"config": config, "summary": summarize(trial)})
        best_index = max(
            range(len(tuning)), key=lambda index: tuning[index]["summary"]["hybrid"]["ndcg_at_5"]
        )
        chosen = CONFIGS[best_index]
    else:
        if selection is None:
            raise ValueError("Test requires an existing frozen dev selection")
        frozen = await asyncio.to_thread(load, selection)
        if (
            frozen.get("split") != "dev"
            or frozen.get("dataset_sha256") != dataset_hash
            or frozen.get("environment_signature") != environment
            or tuple(frozen.get("selected_rrf", [])) not in CONFIGS
        ):
            raise ValueError("Dev selection does not match dataset, code, models or settings")
        selected = frozen["selected_rrf"]
        chosen = (int(selected[0]), float(selected[1]), float(selected[2]))
    rows = await run_rows(service, data, split, chosen, budget)
    report = {
        "version": "m5-heldout-v1",
        "split": split,
        "dataset_sha256": dataset_hash,
        "environment_signature": environment,
        "corpus_fingerprint": data["corpus_fingerprint"],
        "label_origin": data["label_origin"],
        "review": data["review"],
        "selected_rrf": chosen,
        "selection_basis": "dev hybrid nDCG@5 only; fixed tie order",
        "selection_sha256": json_hash(frozen) if split == "test" else None,
        "tuning": tuning,
        "summary": summarize(rows),
        "queries": rows,
        "model_usage": budget_summary(budget),
        "limitations": data["limitations"],
        "latency_note": (
            "End-to-end; cache state is reported, not forcibly cleared. "
            "Rerank fallbacks are included."
        ),
    }
    if corpus_fingerprint(await checked_corpus(service)) != data["corpus_fingerprint"]:
        raise ValueError("Corpus changed during evaluation; discard this run")
    await asyncio.to_thread(save, output, report)
    return {key: value for key, value in report.items() if key != "queries"}
