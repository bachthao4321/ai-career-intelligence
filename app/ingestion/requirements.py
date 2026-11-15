"""Deterministic requirement level for a grounded skill mention.

The classifier only reads wording around the evidence span: a cue in the same sentence
wins over the nearest section heading above it. It never infers proficiency, and anything
without an explicit cue stays ``unspecified``. Cues are Vietnamese, English and Swedish
because those are the current corpus languages.
"""

import re
from dataclasses import dataclass

from app.schemas.ingestion import EvidenceField, Requirement

REQUIREMENTS: tuple[Requirement, ...] = ("required", "preferred", "negated", "unspecified")
REQUIREMENT_VERSION = "requirement-v1"

_FLAGS = re.IGNORECASE | re.UNICODE
_PREFERRED_HEAD = re.compile(
    r"^(?:det är )?(?:meriterande|meritorious|meriting|nice[- ]to[- ]have|good to have|"
    r"bonus|bonus points|preferred|preferences|pluses|plus|desirable|"
    r"vi ser gärna|önskvärt|önskvärda|ưu tiên|điểm cộng|lợi thế)\b",
    _FLAGS,
)
_REQUIRED_HEAD = re.compile(
    r"^(?:krav|skallkrav|skall[- ]krav|obligatoriska krav|kvalifikationer|"
    r"vi söker dig som|din profil|vem är du|vem söker vi|om dig|vi tror att du|vi ser att du|"
    r"för att lyckas|erfarenhet|requirements?|required|qualifications?|minimum qualifications|"
    r"basic qualifications|what you bring|what you'll bring|what you need|who you are|"
    r"your profile|your skills|what we(?:'|’)re looking for|what we are looking for|"
    r"must[- ]haves?|the skills you bring|technical stack & requirements|skills|"
    r"yêu cầu|kỹ năng|tiêu chuẩn|bạn cần có|ứng viên cần)\b",
    _FLAGS,
)
_OTHER_HEAD = re.compile(
    r"^(?:arbetsuppgifter|dina arbetsuppgifter|huvudsakliga arbetsuppgifter|om rollen|"
    r"om tjänsten|om uppdraget|ansökan|övrig|om oss|om företaget|vi erbjuder|vad vi erbjuder|"
    r"vårt erbjudande|du erbjuds|du kommer|i rollen|din roll|ansvarsområden|villkor|"
    r"responsibilities|key responsibilities|about|what you will|what you'll do|the role|"
    r"role summary|job description|job overview|company description|benefits|what we offer|"
    r"why|how to apply|additional information|"
    r"mô tả công việc|trách nhiệm|quyền lợi|phúc lợi|tại sao|về chúng tôi)\b",
    _FLAGS,
)
_PREFERRED_INLINE = re.compile(
    r"\b(?:helst|gärna|not essential|not a must|not mandatory|inte ett krav|inget krav|ej krav|"
    r"nice[- ]to[- ]have|good to have|(?:is |are )?an? (?:big |huge )?plus|"
    r"an advantage|advantageous|beneficial|preferred|preferably|bonus|desirable|ideally|"
    r"meriterande|meriterar|en fördel|önskvärt|ưu tiên|là lợi thế|điểm cộng|là một lợi thế)\b",
    _FLAGS,
)
_REQUIRED_INLINE = re.compile(
    r"\b(?:must|required|requirement|mandatory|essential|krav|skallkrav|skall|måste|"
    r"bắt buộc|yêu cầu|cần có|cần|thành thạo)\b",
    _FLAGS,
)
_NEGATION_BEFORE = re.compile(
    r"\b(?:no|without|not|ingen|inga|utan|không cần|không yêu cầu)\s+(?:[\w+#./-]+\s+){0,2}$",
    _FLAGS,
)
_NEGATION_AFTER = re.compile(
    r"^[\s,:()-]{0,3}(?:[\w+#./-]+\s+){0,2}(?:is |are )?(?:not required|not needed|"
    r"not necessary|krävs inte|behövs inte|không bắt buộc|không cần thiết)\b",
    _FLAGS,
)
_NEGATION_FALSE_POSITIVES = re.compile(
    r"\b(?:not only|no matter|not limited|without any)\b", _FLAGS
)
_BULLET = re.compile(r"^[\s•·*▪◦–-]+")
_HEADING_SPLIT = re.compile(r"[•·▪:]")
# What may follow a heading phrase: nothing, a parenthetical, "?" or a short connective
# such as "om du har" / "som har" / "you have".
_HEADING_TAIL = re.compile(
    r"\s*(?:\([^)]{0,40}\))?\s*(?:(?:om du|att du|som|du|you|bạn)\b[^.]{0,25})?\s*[?!]?\s*",
    _FLAGS,
)
_SENTENCE_BREAK = re.compile(r"(?:[.!?;](?=\s)|[•·▪\n])")


def as_requirement(value: str | None) -> Requirement:
    for item in REQUIREMENTS:
        if value == item:
            return item
    return "unspecified"


@dataclass(frozen=True)
class RequirementLevel:
    requirement: Requirement
    cue: str | None = None


def _sentence(text: str, start: int, end: int) -> tuple[str, str]:
    before = text[:start]
    breaks = list(_SENTENCE_BREAK.finditer(before))
    left = breaks[-1].end() if breaks else 0
    after = _SENTENCE_BREAK.search(text, end)
    right = after.start() if after else len(text)
    return text[left:start], text[end:right]


def _heading(line: str) -> RequirementLevel | None:
    # A heading is a short line, or the part before a bullet/colon on the same line
    # ("Din profil (Skall krav) • ..."). Content lines that merely start with a heading word
    # ("erfarenhet av testautomation med CI/CD") are not headings.
    candidate = _HEADING_SPLIT.split(_BULLET.sub("", line), maxsplit=1)[0].strip()
    if not candidate or len(candidate) > 60 or candidate.endswith("."):
        return None
    levels: tuple[tuple[re.Pattern[str], Requirement], ...] = (
        (_PREFERRED_HEAD, "preferred"),
        (_REQUIRED_HEAD, "required"),
        (_OTHER_HEAD, "unspecified"),
    )
    for pattern, level in levels:
        match = pattern.match(candidate)
        if match and _HEADING_TAIL.fullmatch(candidate[match.end() :]):
            return RequirementLevel(level, None if level == "unspecified" else match.group())
    return None


def _section(text: str, start: int) -> RequirementLevel:
    lines = text[:start].split("\n")
    for index in range(len(lines) - 1, -1, -1):
        raw = lines[index]
        if index != len(lines) - 1 and _BULLET.match(raw):
            continue
        heading = _heading(raw)
        if heading:
            return heading
    return RequirementLevel("unspecified")


def classify_requirement(field: EvidenceField, text: str, start: int, end: int) -> RequirementLevel:
    if field == "title":
        return RequirementLevel("required", "job_title")
    if field != "description" or not 0 <= start < end <= len(text):
        return RequirementLevel("unspecified")
    before, after = _sentence(text, start, end)
    negation = _NEGATION_BEFORE.search(before[-60:]) or _NEGATION_AFTER.search(after[:60])
    if negation and not _NEGATION_FALSE_POSITIVES.search(
        before[-40:] + text[start:end] + after[:40]
    ):
        return RequirementLevel("negated", negation.group().strip()[:80])
    for pattern, level in ((_PREFERRED_INLINE, "preferred"), (_REQUIRED_INLINE, "required")):
        match = pattern.search(before) or pattern.search(after)
        if match:
            return RequirementLevel(as_requirement(level), match.group()[:80])
    return _section(text, start)
