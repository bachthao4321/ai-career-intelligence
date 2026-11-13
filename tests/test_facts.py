import pytest

from app.ingestion.facts import experience_years, salary, seniority


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Python Developer", "senior"),
        ("Sr. Backend Engineer", "senior"),
        ("Tech Lead - Platform", "lead"),
        ("Thực tập sinh Kiểm thử", "intern"),
        ("Senior team intern", "intern"),
        ("Junior Frontend Developer", "junior"),
        ("Fresher Java", "junior"),
        ("Systemutvecklare", "unknown"),
        ("Backend Developer", "unknown"),
    ],
)
def test_seniority_from_title_words(title: str, expected: str) -> None:
    assert seniority(title)[0] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Requirements:\n- 3+ years of experience with Python", 3),
        ("At least five years of professional experience in Java.", 5),
        ("Yêu cầu\n- Có 2 năm kinh nghiệm lập trình", 2),
        ("Du har minst 4 års erfarenhet av systemutveckling.", 4),
        ("2-4 years of backend experience", 2),
        # Preferred and company-history phrases are not candidate minimums.
        ("Nice to have: 5 years of experience with Kubernetes", None),
        ("Med över 20 års erfarenhet hjälper vi kunder.", None),
        ("We have over 15 years of experience in fintech.", None),
        ("Founded 1998, 25 years experience delivering software.", None),
        ("We value experience and curiosity.", None),
    ],
)
def test_experience_years(text: str, expected: int | None) -> None:
    assert experience_years(text)[0] == expected


def test_experience_takes_largest_required_minimum_with_evidence() -> None:
    years, evidence = experience_years(
        "Requirements\n- 2 years of experience with SQL\n- 4 years of experience with Python"
    )
    assert years == 4
    assert evidence is not None and "4 years" in evidence


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Mức lương: 20-30 triệu/tháng.",
            (20_000_000, 30_000_000, "VND", "month"),
        ),
        ("Salary: $1,500 - $2,500 per month", (1_500, 2_500, "USD", "month")),
        ("Salary range 60-80k EUR per year.", (60_000, 80_000, "EUR", "year")),
        ("Månadslön 45 000 kr.", (45_000, 45_000, "SEK", "month")),
        ("Lương thỏa thuận, lên đến 2000 USD", (2_000, 2_000, "USD", None)),
    ],
)
def test_salary_with_keyword_and_currency(
    text: str, expected: tuple[int, int, str, str | None]
) -> None:
    result = salary(text)
    assert (
        result["salary_min"],
        result["salary_max"],
        result["salary_currency"],
        result["salary_period"],
    ) == expected
    assert result["salary_evidence"] in text


@pytest.mark.parametrize(
    "text",
    [
        "Competitive salary.",  # no amount
        "Salary 20-30 per month.",  # no currency
        "We raised $5 million in funding.",  # no salary keyword
        "Salary review twice a year and a 13th month salary bonus of 10 000 USD.",
        "Omsättning 300 MSEK. Lön enligt överenskommelse.",
        "Salary: up to 15% annual bonus",
    ],
)
def test_salary_requires_keyword_currency_and_amount(text: str) -> None:
    assert salary(text) == {}
