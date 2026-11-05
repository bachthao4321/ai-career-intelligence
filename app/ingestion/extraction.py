import re
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.ingestion.contracts import EvidenceField, JobRecord, SkillMention, alias_key, json_hash

EXTRACTION_VERSION = "grounded-v2"
NON_SKILL_CONTEXT = re.compile(
    r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.IGNORECASE
)


def inside_link_or_email(text: str, start: int, end: int) -> bool:
    return any(
        match.start() <= start and end <= match.end() for match in NON_SKILL_CONTEXT.finditer(text)
    )


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    aliases: list[str]


class SkillCatalog:
    def __init__(self, definitions: list[SkillDefinition], approved: dict[str, str] | None = None):
        self.definitions = definitions
        self.names = {definition.slug: definition.name for definition in definitions}
        self.aliases: dict[str, str] = {}
        self.surfaces: dict[str, str] = {}
        for definition in definitions:
            for surface in definition.aliases:
                self.add_alias(surface, definition.slug)
        for surface, slug in (approved or {}).items():
            self.add_alias(surface, slug)
        self.version = json_hash(
            {"definitions": [d.model_dump() for d in definitions], "aliases": self.aliases}
        )
        self.pattern = re.compile(
            r"(?<![\w])(?:"
            + "|".join(
                re.escape(value) for value in sorted(self.surfaces.values(), key=len, reverse=True)
            )
            + r")(?![\w])",
            re.IGNORECASE,
        )

    def add_alias(self, surface: str, slug: str) -> None:
        key = alias_key(surface)
        if not key or slug not in self.names:
            raise ValueError("Alias requires a known skill and a non-empty surface")
        if key in self.aliases and self.aliases[key] != slug:
            raise ValueError("Conflicting skill alias")
        self.aliases[key] = slug
        self.surfaces.setdefault(key, surface)

    @classmethod
    def load(cls, approved: dict[str, str] | None = None) -> "SkillCatalog":
        content = files("app.ingestion").joinpath("skill_catalog.json").read_text(encoding="utf-8")
        return cls(TypeAdapter(list[SkillDefinition]).validate_json(content), approved)


@dataclass
class GroundingResult:
    accepted: list[SkillMention] = field(default_factory=list)
    alias_candidates: list[SkillMention] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)


def validate_mentions(
    record: JobRecord,
    mentions: list[SkillMention],
    catalog: SkillCatalog,
    *,
    alias_confidence: float = 0.95,
) -> GroundingResult:
    result = GroundingResult()
    seen: set[str] = set()
    for mention in mentions:
        text = record.evidence_text(mention.source_field)
        reason: str | None = None
        if mention.skill not in catalog.names:
            reason = "unknown_canonical_skill"
        elif (
            mention.end <= mention.start
            or mention.end > len(text)
            or text[mention.start : mention.end] != mention.evidence
        ):
            reason = "evidence_span_mismatch"
        elif (mention.start > 0 and re.match(r"\w", text[mention.start - 1])) or (
            mention.end < len(text) and re.match(r"\w", text[mention.end])
        ):
            reason = "evidence_is_substring_of_another_word"
        elif inside_link_or_email(text, mention.start, mention.end):
            reason = "evidence_is_inside_link_or_email"
        else:
            normalized = alias_key(mention.evidence)
            if len(normalized) > 100:
                result.rejected.append(
                    {"skill": mention.skill, "reason": "normalized_alias_too_long"}
                )
                continue
            mapped = catalog.aliases.get(normalized)
            if mapped is None:
                if mention.confidence >= alias_confidence:
                    result.alias_candidates.append(mention)
                else:
                    reason = "alias_below_review_threshold"
            elif mapped != mention.skill:
                reason = "alias_conflicts_with_canonical_skill"
            elif any(
                match.start() < mention.end
                and mention.start < match.end()
                and catalog.aliases.get(alias_key(match.group())) != mention.skill
                for match in catalog.pattern.finditer(text)
            ):
                reason = "evidence_overlaps_a_more_specific_skill"
            elif mention.skill not in seen:
                result.accepted.append(mention)
                seen.add(mention.skill)
        if reason:
            result.rejected.append({"skill": mention.skill, "reason": reason})
    return result


def deterministic_extract(record: JobRecord, catalog: SkillCatalog) -> list[SkillMention]:
    mentions: list[SkillMention] = []
    fields: tuple[EvidenceField, ...] = ("structured_skills", "title", "description")
    # Longer aliases win overlapping matches (e.g. ASP.NET Core before .NET).
    seen: set[str] = set()
    for source_field in fields:
        text = record.evidence_text(source_field)
        for match in catalog.pattern.finditer(text):
            if inside_link_or_email(text, match.start(), match.end()):
                continue
            slug = catalog.aliases.get(alias_key(match.group()))
            if slug is None or slug in seen:
                continue
            seen.add(slug)
            mentions.append(
                SkillMention(
                    skill=slug,
                    source_field=source_field,
                    evidence=match.group(),
                    start=match.start(),
                    end=match.end(),
                    confidence=1.0,
                )
            )
    return mentions


ExtractorKind = Literal["deterministic", "deepseek"]
