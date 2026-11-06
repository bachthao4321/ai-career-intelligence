import asyncio
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from app.ingestion.contracts import json_hash
from app.retrieval.contracts import SearchDocument, SearchFilters, SearchRequest
from app.retrieval.documents import deduplicate, load_documents
from app.retrieval.service import SearchService


def corpus_fingerprint(documents: list[SearchDocument]) -> str:
    return json_hash(
        sorted(
            (doc.source, doc.source_job_id, doc.content_hash, doc.index_hash) for doc in documents
        )
    )


def prepare_pilot(
    documents: list[SearchDocument], families_path: Path, output: Path
) -> dict[str, Any]:
    if not documents or any(doc.source != "jobtech" or doc.private for doc in documents):
        raise ValueError("Pilot labels require the JobTech-only non-private corpus")
    documents, _ = deduplicate(documents)
    families = json.loads(families_path.read_text(encoding="utf-8"))
    relevance = {}
    cases = []
    for family in families:
        relevant = {doc.content_hash: 1 for doc in documents if family["skill"] in doc.skills}
        if not relevant:
            raise ValueError("Each pilot family must have at least one literal-skill match")
        relevance[family["id"]] = relevant
        for language, query in family["queries"].items():
            cases.append(
                {
                    "id": family["id"] + "_" + language,
                    "family": family["id"],
                    "split": family["split"],
                    "language": language,
                    "style": "keyword" if language == "vi" else "rephrased",
                    "query": query,
                }
            )
    dataset = {
        "version": "m2-jobtech-pilot-v1",
        "label_origin": "silver_literal_skill_mentions_v1",
        "limitations": (
            "Not human semantic relevance labels; shared fixed corpus, "
            "query families split before tuning."
        ),
        "filters": {"source": "jobtech"},
        "corpus_fingerprint": corpus_fingerprint(documents),
        "unique_documents": len(documents),
        "relevance": relevance,
        "queries": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(dataset, stream, ensure_ascii=False, indent=2)
    return {
        "output": str(output),
        "queries": len(cases),
        "unique_documents": len(documents),
        "label_origin": dataset["label_origin"],
    }


def retrieval_metrics(
    ranked: list[str], relevance: dict[str, int], *, k: int = 5
) -> dict[str, float]:
    if k < 1 or len(ranked) != len(set(ranked)):
        raise ValueError("Metrics require positive k and deduplicated result identities")
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in relevance.values()):
        raise ValueError("Relevance grades must be integers from 0 to 3")
    positives = {key for key, grade in relevance.items() if grade > 0}
    gains = [relevance.get(key, 0) for key in ranked[:k]]
    ideal = sorted(relevance.values(), reverse=True)[:k]
    dcg = sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(gains))
    idcg = sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
    found = sum(grade > 0 for grade in gains)
    return {
        "precision_at_5": found / k,
        "recall_at_5": found / len(positives) if positives else 0,
        "ndcg_at_5": dcg / idcg if idcg else 0,
        "precision_ceiling_at_5": min(k, len(positives)) / k,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {}
    result = {key: mean(row["metrics"][key] for row in rows) for key in rows[0]["metrics"]}
    result.update(
        latency_p50_ms=float(np.quantile([row["latency_ms"] for row in rows], 0.5)),
        latency_p95_ms=float(np.quantile([row["latency_ms"] for row in rows], 0.95)),
        queries=len(rows),
    )
    return result


def load_dataset(dataset_path: Path, output: Path) -> tuple[dict[str, Any], str]:
    if not output.resolve().is_relative_to(Path("data/evaluations").resolve()):
        raise ValueError("Evaluation outputs must be under data/evaluations/")
    raw = dataset_path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError("Evaluation dataset exceeds 2 MB")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a dataset object")
    return data, hashlib.sha256(raw).hexdigest()


def write_evaluation(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def evaluate(service: SearchService, dataset_path: Path, output: Path) -> dict[str, Any]:
    data, dataset_hash = await asyncio.to_thread(load_dataset, dataset_path, output)
    filters = SearchFilters.model_validate(data["filters"])
    docs, _ = deduplicate(await load_documents(service.engine, service.settings, filters))
    if corpus_fingerprint(docs) != data["corpus_fingerprint"]:
        raise ValueError("Frozen pilot corpus fingerprint changed; create a new dataset version")
    queries = data["queries"]
    if len(queries) > 200 or any(query["split"] not in {"dev", "test"} for query in queries):
        raise ValueError("Invalid evaluation cases")
    dev_families = {query["family"] for query in queries if query["split"] == "dev"}
    test_families = {query["family"] for query in queries if query["split"] == "test"}
    if dev_families & test_families:
        raise ValueError("Query families must not leak between dev/test")
    rows: list[dict[str, Any]] = []

    async def run(
        query: dict[str, Any], mode: str, config: tuple[int, float, float]
    ) -> dict[str, Any]:
        response = await service.search(
            SearchRequest(query=query["query"], mode=mode, top_k=5, filters=filters),
            rrf_k=config[0],
            rrf_weights=(config[1], config[2]),
        )
        if response.effective_mode != mode or (
            mode != "bm25" and response.indexed_documents != len(docs)
        ):
            raise ValueError("Benchmark requires a complete, non-degraded index")
        hashes = [hit.content_hash for hit in response.items]
        return {
            "id": query["id"],
            "family": query["family"],
            "split": query["split"],
            "language": query["language"],
            "style": query["style"],
            "mode": mode,
            "latency_ms": response.latency_ms,
            "metrics": retrieval_metrics(hashes, data["relevance"][query["family"]]),
            "results": hashes,
        }

    configs = [(60, 1.0, 1.0), (20, 1.0, 1.0), (100, 1.0, 1.0), (60, 0.5, 1.0)]
    tuning: list[dict[str, Any]] = []
    for config in configs:
        trial = [await run(query, "hybrid", config) for query in queries if query["split"] == "dev"]
        tuning.append({"config": config, "metrics": aggregate(trial)})
    chosen = max(range(len(tuning)), key=lambda index: tuning[index]["metrics"]["ndcg_at_5"])
    selected = configs[chosen]
    for query in queries:
        for mode in ("bm25", "dense", "hybrid"):
            rows.append(await run(query, mode, selected))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[f"{row['split']}/{row['mode']}/all"].append(row)
        grouped[f"{row['split']}/{row['mode']}/{row['language']}"].append(row)
    report = {
        "dataset_sha256": dataset_hash,
        "corpus_fingerprint": data["corpus_fingerprint"],
        "label_origin": data["label_origin"],
        "model_signature": service.embedder.signature,
        "selected_on_dev_only": selected,
        "tuning": tuning,
        "summary": {key: aggregate(group) for key, group in grouped.items()},
        "queries": rows,
        "limits": data["limitations"],
    }
    await asyncio.to_thread(write_evaluation, output, report)
    return {key: value for key, value in report.items() if key != "queries"}
