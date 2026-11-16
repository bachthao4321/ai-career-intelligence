from collections import defaultdict

from app.ingestion.extraction import SkillCatalog, inside_link_or_email
from app.schemas.cv import CVEvidence, CVPage, CVProfile, CVSkill
from app.schemas.ingestion import alias_key

EXTRACTION_VERSION = "cv-literal-v1"


def extract_skills(pages: list[CVPage], warnings: list[str]) -> CVProfile:
    catalog = SkillCatalog.load()
    mentions: dict[str, list[CVEvidence]] = defaultdict(list)
    for page in pages:
        for match in catalog.pattern.finditer(page.text):
            if inside_link_or_email(page.text, match.start(), match.end()):
                continue
            slug = catalog.aliases[alias_key(match.group())]
            if len(mentions[slug]) < 5:
                mentions[slug].append(
                    CVEvidence(
                        page=page.number, start=match.start(), end=match.end(), text=match.group()
                    )
                )
    if not mentions:
        warnings = [*warnings, "no_curated_skills_found_add_target_role_or_review_cv_text"]
    return CVProfile(
        pages=pages,
        skills=[
            CVSkill(slug=slug, name=catalog.names[slug], evidence=mentions[slug])
            for slug in sorted(mentions)
        ],
        warnings=[
            *warnings,
            "literal_mentions_not_verified_proficiency_or_negation",
            "pdf_reading_order_may_differ_from_visual_layout",
        ],
        extraction_version=EXTRACTION_VERSION,
        catalog_version=catalog.version,
    )
