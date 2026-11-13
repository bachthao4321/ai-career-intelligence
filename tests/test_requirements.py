import pytest

from app.ingestion.requirements import classify_requirement


def level(text: str, skill: str, field: str = "description") -> tuple[str, str | None]:
    start = text.index(skill)
    result = classify_requirement(field, text, start, start + len(skill))  # type: ignore[arg-type]
    return result.requirement, result.cue


@pytest.mark.parametrize(
    ("text", "skill", "expected"),
    [
        ("Requirements:\n• Python\n• Docker", "Docker", "required"),
        ("Krav\nErfarenhet av Java", "Java", "required"),
        ("Din profil (Skall krav) • Flerårig erfarenhet av Java", "Java", "required"),
        ("Yêu cầu\n- Thành thạo Python", "Python", "required"),
        ("Meriterande\n• Kubernetes\n• Terraform", "Terraform", "preferred"),
        ("Det är meriterande om du har\n• erfarenhet av AWS", "AWS", "preferred"),
        ("Nice to have:\n- GraphQL", "GraphQL", "preferred"),
        ("Ưu tiên\nCó kinh nghiệm Docker", "Docker", "preferred"),
        ("Requirements\n- Python\n- React is a plus", "React", "preferred"),
        ("Experience with AWS is valuable, but not essential.", "AWS", "preferred"),
        ("You must know SQL well.", "SQL", "required"),
        ("Cần Python, Postgres và Kubernetes.", "Kubernetes", "required"),
        ("Không cần Java.", "Java", "negated"),
        ("No Java experience needed. Python required.", "Java", "negated"),
        ("Kotlin is not required for this role.", "Kotlin", "negated"),
        ("Responsibilities\n- Build services in Go", "Go", "unspecified"),
        ("We use Python and Docker every day.", "Docker", "unspecified"),
        # A content line starting with a heading word is not itself a heading.
        ("Om rollen\nerfarenhet av testautomation med CI/CD", "CI/CD", "unspecified"),
        # A later section resets an earlier requirements heading.
        ("Requirements\n- Python\nBenefits\n- Free Docker stickers", "Docker", "unspecified"),
        ("Not only Python but also SQL.", "Python", "unspecified"),
    ],
)
def test_requirement_cues(text: str, skill: str, expected: str) -> None:
    assert level(text, skill)[0] == expected


def test_title_mentions_are_required_and_tags_unspecified() -> None:
    assert level("Senior Python Developer", "Python", "title") == ("required", "job_title")
    assert level("python\ndocker", "docker", "structured_skills") == ("unspecified", None)


def test_cue_is_evidence_text_and_invalid_offsets_fail_closed() -> None:
    assert level("Meriterande\n• Kubernetes", "Kubernetes") == ("preferred", "Meriterande")
    assert classify_requirement("description", "Python", 3, 99).requirement == "unspecified"
