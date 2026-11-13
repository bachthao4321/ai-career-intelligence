"""Structured job facts with literal evidence: seniority, experience, salary, deadline.

Every value is either copied from an explicit phrase (kept as evidence) or left empty.
Nothing is inferred from other fields: seniority comes from title words only, and a
salary needs both a salary keyword and a currency in the same sentence.
"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ingestion.contracts import JobRecord
from app.ingestion.presentation import application_deadline
from app.ingestion.requirements import classify_requirement

FACTS_VERSION = "facts-v1"
Seniority = Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
SENIORITIES: tuple[Seniority, ...] = ("intern", "junior", "mid", "senior", "lead", "unknown")
SalaryPeriod = Literal["hour", "month", "year"]
_FLAGS = re.IGNORECASE | re.UNICODE

# Checked in order: an intern posting titled "Senior team intern" stays intern.
_SENIORITY_PATTERNS: tuple[tuple[Seniority, re.Pattern[str]], ...] = (
    (
        "intern",
        re.compile(
            r"\b(?:intern|internship|praktikant|praktik|thực tập(?: sinh)?|trainee)\b", _FLAGS
        ),
    ),
    (
        "lead",
        re.compile(
            r"\b(?:lead|leader|tech lead|team lead|principal|staff engineer|head of|director|"
            r"engineering manager|chief|cto|trưởng nhóm|trưởng phòng|giám đốc|teamledare|"
            r"gruppchef|chef)\b",
            _FLAGS,
        ),
    ),
    ("senior", re.compile(r"\b(?:senior|sr\.?|seniör|erfaren)\b", _FLAGS)),
    ("mid", re.compile(r"\b(?:mid|middle|mid-level|intermediate)\b", _FLAGS)),
    (
        "junior",
        re.compile(
            r"\b(?:junior|jr\.?|fresher|entry[- ]level|graduate|nyexaminerad|mới tốt nghiệp)\b",
            _FLAGS,
        ),
    ),
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "en": 1, "ett": 1, "två": 2, "tre": 3, "fyra": 4, "fem": 5,
    "sex": 6, "sju": 7, "åtta": 8, "nio": 9, "tio": 10, "một": 1, "hai": 2, "ba": 3,
    "bốn": 4, "năm": 5,
}  # fmt: skip
_COUNT = r"(\d{1,2}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"
_EXPERIENCE = re.compile(
    _COUNT
    + r"\s*\+?\s*(?:(?:-|–|to|till|đến)\s*"
    + _COUNT
    + r"\s*\+?\s*)?(?:years?|yrs?|års?|år|năm)\b(?:\s+|['’]\s*)(?:of\s+)?(?:[\w/&-]+\s+){0,4}?"
    r"(?:\w*erfarenhet|experience|kinh nghiệm)",
    _FLAGS,
)

# "Med över 20 års erfarenhet" / "we have over 15 years of experience" describe the company.
_COMPANY_TENURE = re.compile(
    r"\b(?:med|with|for|har|has|have|có)\s+(?:över|snart|mer än|more than|over|nearly|almost|"
    r"hơn)\s*$",
    _FLAGS,
)
_SALARY_KEYWORD = re.compile(
    r"\b(?:salary|salaries|compensation|pay range|base pay|lương|thu nhập|mức lương|lön|"
    r"månadslön|årslön|grundlön|ingångslön)",
    _FLAGS,
)
_SALARY_EXCLUDE = re.compile(
    r"\b(?:bonus|benefit|friskvård|phúc lợi|omsätter|omsättning|revenue|funding|"
    r"tháng lương thứ|13th)",
    _FLAGS,
)
_AMOUNT = r"(\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?)\s*(k|tr|triệu|million|m)?"
_PREFIX = r"(\$|us\$|usd|€|eur|sek|£|gbp|vnd)\s*"
_SUFFIX = r"\s*(usd|\$|eur|€|sek|kr|kronor|vnd|đ|đồng|gbp|£)?"
_SALARY = re.compile(
    r"(?:" + _PREFIX + r")?" + _AMOUNT + r"(?:\s*(?:-|–|to|till|đến)\s*(?:" + _PREFIX + r")?"
    + _AMOUNT + r")?" + _SUFFIX + r"(?![\w%])",
    _FLAGS,
)  # fmt: skip
_CURRENCIES = {
    "$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "sek": "SEK",
    "kr": "SEK", "kronor": "SEK", "vnd": "VND", "đ": "VND", "đồng": "VND", "£": "GBP",
    "gbp": "GBP", "tr": "VND", "triệu": "VND",
}  # fmt: skip
_MULTIPLIERS = {
    "k": 1_000,
    "tr": 1_000_000,
    "triệu": 1_000_000,
    "million": 1_000_000,
    "m": 1_000_000,
}
_PERIODS: tuple[tuple[SalaryPeriod, re.Pattern[str]], ...] = (
    ("hour", re.compile(r"(?:/\s*h\b|/\s*hour|per hour|hourly|/\s*tim|timlön|/\s*giờ)", _FLAGS)),
    (
        "month",
        re.compile(
            r"(?:/\s*month|per month|monthly|a month|/\s*mån|månadslön|per månad|/\s*tháng|"
            r"mỗi tháng|hàng tháng|net/month|gross/month)",
            _FLAGS,
        ),
    ),
    (
        "year",
        re.compile(
            r"(?:/\s*year|per year|per annum|annual|annually|a year|/\s*år|årslön|per år|"
            r"/\s*năm|mỗi năm)",
            _FLAGS,
        ),
    ),
)
_SENTENCE = re.compile(r"[^.!?\n•]+(?:[.!?](?!\d)[^.!?\n•]*)*")


class JobFacts(BaseModel):
    version: str = FACTS_VERSION
    seniority: Seniority = "unknown"
    seniority_evidence: str | None = Field(default=None, max_length=80)
    experience_years_min: int | None = Field(default=None, ge=0, le=30)
    experience_evidence: str | None = Field(default=None, max_length=160)
    salary_min: int | None = Field(default=None, gt=0)
    salary_max: int | None = Field(default=None, gt=0)
    salary_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    salary_period: SalaryPeriod | None = None
    salary_evidence: str | None = Field(default=None, max_length=200)
    application_deadline: date | None = None


def _count(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.isdecimal() else _NUMBER_WORDS.get(value.casefold())


def seniority(title: str) -> tuple[Seniority, str | None]:
    for level, pattern in _SENIORITY_PATTERNS:
        match = pattern.search(title)
        if match:
            return level, match.group()
    return "unknown", None


def experience_years(description: str) -> tuple[int | None, str | None]:
    """Largest explicit minimum in required/unspecified context; preferred cues ignored."""
    best: tuple[int, str] | None = None
    for match in _EXPERIENCE.finditer(description):
        level = classify_requirement("description", description, match.start(), match.end())
        if level.requirement in {"preferred", "negated"}:
            continue
        if _COMPANY_TENURE.search(description[max(0, match.start() - 25) : match.start()]):
            continue
        low = _count(match.group(1))
        # Above 20 years is almost always company history, not a candidate requirement.
        if low is None or not 0 <= low <= 20:
            continue
        if best is None or low > best[0]:
            best = (low, match.group()[:160])
    return best if best else (None, None)


def _amount(number: str, multiplier: str | None) -> int | None:
    digits = number.replace(" ", " ").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits):
        value = float(re.sub(r"[.,]", "", digits))
    else:
        value = float(digits.replace(",", "."))
    value *= _MULTIPLIERS.get((multiplier or "").casefold(), 1)
    return int(value) if value >= 1 else None


def salary(description: str) -> dict[str, Any]:
    for sentence_match in _SENTENCE.finditer(description):
        sentence = sentence_match.group()
        if not _SALARY_KEYWORD.search(sentence) or _SALARY_EXCLUDE.search(sentence):
            continue
        for match in _SALARY.finditer(sentence):
            prefix1, low, mult1, prefix2, high, mult2, suffix = match.groups()
            # Range multipliers apply to both ends: "20-30 triệu", "60-80k".
            mult1 = mult1 or mult2
            currency_token = (prefix1 or prefix2 or suffix or mult2 or mult1 or "").casefold()
            currency = _CURRENCIES.get(currency_token)
            if currency is None:
                continue
            lowest = _amount(low, mult1)
            highest = _amount(high, mult2) if high else lowest
            if lowest is None or highest is None or highest < lowest:
                continue
            # Single numbers below 100 are usually percentages, ages or counts.
            if highest < 100:
                continue
            period = next((name for name, pattern in _PERIODS if pattern.search(sentence)), None)
            return {
                "salary_min": lowest,
                "salary_max": highest,
                "salary_currency": currency,
                "salary_period": period,
                "salary_evidence": sentence.strip()[:200],
            }
    return {}


def facts_from(title: str, description: str, source: str, raw_payload: dict[str, Any]) -> JobFacts:
    level, level_evidence = seniority(title)
    years, years_evidence = experience_years(description)
    return JobFacts(
        seniority=level,
        seniority_evidence=level_evidence,
        experience_years_min=years,
        experience_evidence=years_evidence,
        application_deadline=application_deadline(source, raw_payload),
        **salary(description),
    )


def extract_facts(record: JobRecord) -> JobFacts:
    return facts_from(record.title, record.description, record.source, record.raw_payload)
