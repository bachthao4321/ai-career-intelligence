import math
import re
import unicodedata
import uuid
from collections import Counter

from app.schemas.search import RankedHit, SearchDocument

TOKEN = re.compile(r"(?:\.[^\W_]+|[^\W_]+)(?:[./#-][^\W_]+)*[+#]*", re.UNICODE)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "can",
    "tim",
    "viec",
    "lam",
    "mot",
    "cac",
    "co",
    "va",
    "cho",
    "voi",
    "o",
    "tai",
    "trong",
    "biet",
    "och",
    "att",
    "i",
    "en",
    "ett",
    "som",
    "med",
    "pa",
    "till",
    "av",
    "ar",
    "fran",
}


def tokenize(text: str) -> list[str]:
    lowered = unicodedata.normalize("NFD", text.casefold()).replace("đ", "d")
    lowered = "".join(character for character in lowered if not unicodedata.combining(character))
    return [token for token in TOKEN.findall(lowered) if token not in STOPWORDS]


def bm25(query: str, documents: list[SearchDocument], *, limit: int = 50) -> list[RankedHit]:
    """Positive-IDF BM25, k1=1.5, b=0.75; corpus is filtered and deduplicated first."""
    terms = set(tokenize(query))
    if not terms or not documents:
        return []
    counters = {doc.job_id: Counter(tokenize(doc.text)) for doc in documents}
    lengths = {key: sum(counter.values()) for key, counter in counters.items()}
    average = sum(lengths.values()) / len(documents) or 1
    frequencies = {term: sum(term in counter for counter in counters.values()) for term in terms}
    hits: list[RankedHit] = []
    for document in documents:
        counter = counters[document.job_id]
        score = 0.0
        for term in terms:
            frequency = counter[term]
            if frequency:
                idf = math.log(
                    1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                score += (
                    idf
                    * frequency
                    * 2.5
                    / (frequency + 1.5 * (0.25 + 0.75 * lengths[document.job_id] / average))
                )
        if score > 0:
            hits.append(RankedHit(document.job_id, score))
    return sorted(hits, key=lambda value: (-value.score, str(value.job_id)))[:limit]


def reciprocal_rank_fusion(
    rankings: list[list[RankedHit]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[RankedHit]:
    if k < 1 or (
        weights is not None
        and (
            len(weights) != len(rankings)
            or any(w < 0 or not math.isfinite(w) for w in weights)
            or not any(weights)
        )
    ):
        raise ValueError("Invalid RRF parameters")
    totals: dict[uuid.UUID, float] = {}
    for ranking, weight in zip(rankings, weights or [1.0] * len(rankings), strict=True):
        seen: set[uuid.UUID] = set()
        for hit in ranking:
            if hit.job_id in seen:
                continue
            seen.add(hit.job_id)
            totals[hit.job_id] = totals.get(hit.job_id, 0) + weight / (k + len(seen))
    return sorted(
        (RankedHit(key, score) for key, score in totals.items()),
        key=lambda value: (-value.score, str(value.job_id)),
    )
