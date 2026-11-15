import uuid
from datetime import datetime

from app.schemas.cv import (
    CVJobMatch,
    CVProfile,
    MatchedSkill,
    MatchReport,
    MatchRequest,
    NotSeenSkill,
)
from app.schemas.search import SearchDocument, SourceSkillEvidence
from app.services.search.documents import deduplicate
from app.services.search.lexical import bm25

# Explicitly required job skills count fully; "nice to have" counts half. Negated
# mentions ("no Java needed") are not job skills and never become gaps.
REQUIREMENT_WEIGHTS = {"required": 1.0, "unspecified": 0.75, "preferred": 0.5}
REQUIREMENT_ORDER = {"required": 0, "unspecified": 1, "preferred": 2}


def weighted_coverage(job_skills: dict[str, SourceSkillEvidence], shared: set[str]) -> float | None:
    total = sum(REQUIREMENT_WEIGHTS[item.requirement] for item in job_skills.values())
    if not total:
        return None
    return sum(REQUIREMENT_WEIGHTS[job_skills[slug].requirement] for slug in shared) / total


def match_jobs(
    cv_id: uuid.UUID,
    expires_at: datetime,
    profile: CVProfile,
    request: MatchRequest,
    all_docs: list[SearchDocument],
) -> MatchReport:
    docs, counts = deduplicate(all_docs)
    cv_skills = {item.slug: item for item in profile.skills}
    # Only canonical skills and explicit target role enter retrieval, never full CV/identity.
    query = " ".join([request.target_role or "", *sorted(cv_skills)])[:500]
    lexical = bm25(query, docs, limit=50) if query.strip() else []
    lexical_scores = {hit.job_id: hit.score for hit in lexical}
    evidence = {
        doc.job_id: {
            item.slug: item for item in doc.skill_evidence if item.requirement != "negated"
        }
        for doc in docs
    }
    negated = {
        doc.job_id: {item.slug for item in doc.skill_evidence if item.requirement == "negated"}
        for doc in docs
    }
    overlaps = {doc.job_id: set(evidence[doc.job_id]) & cv_skills.keys() for doc in docs}
    coverage = {
        doc.job_id: weighted_coverage(evidence[doc.job_id], overlaps[doc.job_id]) for doc in docs
    }
    skill_candidates = sorted(
        (doc for doc in docs if overlaps[doc.job_id]),
        key=lambda doc: (-(coverage[doc.job_id] or 0), -len(overlaps[doc.job_id]), str(doc.job_id)),
    )[:50]
    pool = set(lexical_scores) | {doc.job_id for doc in skill_candidates}
    max_bm25 = max(lexical_scores.values(), default=1.0)
    ranked = sorted(
        (doc for doc in docs if doc.job_id in pool),
        key=lambda doc: (
            -(
                0.7 * (coverage[doc.job_id] or 0)
                + 0.3 * lexical_scores.get(doc.job_id, 0) / max_bm25
            ),
            -len(overlaps[doc.job_id]),
            str(doc.job_id),
        ),
    )
    items = []
    for doc in ranked[: request.top_k]:
        job_skills = evidence[doc.job_id]
        shared = overlaps[doc.job_id]
        score = (
            0.7 * (coverage[doc.job_id] or 0) + 0.3 * lexical_scores.get(doc.job_id, 0) / max_bm25
        )
        items.append(
            CVJobMatch(
                job_id=doc.job_id,
                content_hash=doc.content_hash,
                title=doc.title,
                company=doc.company,
                location=doc.location,
                source=doc.source,
                source_url=doc.source_url,
                usage_scope="local_research_only" if doc.private else "source_terms_apply",
                duplicate_listings=counts[doc.content_hash],
                score=round(score, 6),
                bm25_score=lexical_scores.get(doc.job_id, 0),
                skill_coverage=coverage[doc.job_id],
                matched_skills=[
                    MatchedSkill(
                        slug=slug,
                        cv_evidence=cv_skills[slug].evidence,
                        job_evidence=job_skills[slug],
                    )
                    for slug in sorted(shared)
                ],
                skills_not_seen_in_cv=[
                    NotSeenSkill(
                        slug=slug,
                        requirement=job_skills[slug].requirement,
                        job_evidence=job_skills[slug],
                    )
                    for slug in sorted(
                        job_skills.keys() - cv_skills.keys(),
                        key=lambda value: (
                            REQUIREMENT_ORDER[job_skills[value].requirement],
                            value,
                        ),
                    )
                ],
                negated_job_skills=sorted(negated[doc.job_id]),
                cv_skills_not_mentioned_in_job=sorted(cv_skills.keys() - job_skills.keys()),
            )
        )
    warnings = [
        *profile.warnings,
        "scores_are_uncalibrated_not_hiring_probabilities",
        "skills_not_seen_in_cv_are_not_proven_missing_or_required",
        "only_curated_literal_skills_compared_no_seniority_or_experience_inference",
        "requirement_levels_from_wording_cues_unspecified_means_no_cue",
    ]
    if not profile.skills:
        warnings.append("no_cv_skills_target_role_only_or_no_matches")
    if any(set(doc.skills) != {item.slug for item in doc.skill_evidence} for doc in docs):
        warnings.append("some_job_skill_evidence_invalid_excluded_from_comparison")
    if any(doc.truncated for doc in docs):
        warnings.append("bm25_document_text_limited_to_20000_characters")
    if not ranked:
        warnings.append("no_matches_in_filtered_corpus")
    return MatchReport(
        cv_id=cv_id,
        expires_at=expires_at,
        eligible_listings=len(all_docs),
        unique_documents=len(docs),
        candidate_documents=len(pool),
        filters=request.filters,
        items=items,
        warnings=warnings,
    )
