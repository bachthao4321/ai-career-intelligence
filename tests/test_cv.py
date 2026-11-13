import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.cv.contracts import MAX_PDF_BYTES, CVError, CVPage, MatchRequest
from app.cv.extraction import extract_skills
from app.cv.matching import match_jobs
from app.cv.parser import parse_pdf
from app.main import create_app
from app.retrieval.contracts import SourceSkillEvidence
from tests.cv_helpers import SYNTHETIC_CV, pdf_bytes
from tests.retrieval_helpers import document


async def test_real_worker_extracts_aliases_and_exact_page_evidence() -> None:
    profile = await parse_pdf(
        pdf_bytes([SYNTHETIC_CV, "Second page experience in Python services."])
    )
    assert {skill.slug for skill in profile.skills} == {"python", "postgresql"}
    assert len(profile.pages) == 2
    for skill in profile.skills:
        for span in skill.evidence:
            assert profile.pages[span.page - 1].text[span.start : span.end] == span.text
    assert next(s for s in profile.skills if s.slug == "postgresql").evidence[0].text == "Postgres"


@pytest.mark.parametrize(
    "data,code",
    [
        (b"not a PDF", "invalid_or_oversize_pdf"),
        (b"%PDF-1.7\nmalformed PRIVATE SENTINEL", "invalid_pdf"),
        (b"%PDF-" + b"a" * MAX_PDF_BYTES, "invalid_or_oversize_pdf"),
        (pdf_bytes([""]), "no_extractable_text"),
        (pdf_bytes([SYNTHETIC_CV] * 11), "pdf_page_limit"),
        (pdf_bytes(password="fixture-password"), "encrypted_pdf"),
        (pdf_bytes(["A" * 41000]), "pdf_text_limit"),
        (pdf_bytes(compressed_content=b" " * (5 * 1024 * 1024)), "invalid_pdf"),
    ],
)
async def test_invalid_pdf_cases_return_safe_codes(data: bytes, code: str) -> None:
    with pytest.raises(CVError, match=code) as error:
        await parse_pdf(data)
    assert "PRIVATE SENTINEL" not in str(error.value)


async def test_mixed_blank_pages_warn_instead_of_claiming_complete_cv() -> None:
    profile = await parse_pdf(pdf_bytes([SYNTHETIC_CV, ""]))
    assert "some_pages_without_text_report_may_be_incomplete" in profile.warnings


def test_unicode_overlaps_urls_and_negation_are_explicit() -> None:
    text = (
        "Kỹ năng: Python, Postgres, ASP.NET Core. https://python.org x@docker.io. Không biết Java."
    )
    profile = extract_skills([CVPage(number=1, text=text)], [])
    slugs = {skill.slug for skill in profile.skills}
    assert {"python", "postgresql", "java"} <= slugs
    assert "docker" not in slugs
    assert "literal_mentions_not_verified_proficiency_or_negation" in profile.warnings
    for skill in profile.skills:
        for span in skill.evidence:
            assert text[span.start : span.end] == span.text
    assert (
        max(
            len(s.evidence)
            for s in extract_skills([CVPage(number=1, text="Python " * 20)], []).skills
        )
        == 5
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_timeout_or_cancel_kills_and_reaps_process(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    process = Mock(returncode=None)
    calls = 0

    async def communicate(data: bytes | None = None) -> tuple[bytes, bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.sleep(100)
        return b"", b""

    process.communicate = communicate
    monkeypatch.setattr(
        "app.cv.parser.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr("app.cv.parser.PARSER_TIMEOUT_SECONDS", 0.01 if not cancel else 10)
    task = asyncio.create_task(parse_pdf(b"%PDF-1.7"))
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(CVError, match="pdf_parser_timeout"):
            await task
    process.kill.assert_called_once()
    assert calls == 2


async def test_parent_revalidates_worker_evidence_and_does_not_pass_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = extract_skills([CVPage(number=1, text=SYNTHETIC_CV)], []).model_dump(mode="json")
    profile["skills"][0]["evidence"][0]["text"] = "forged"
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(json.dumps({"profile": profile}).encode(), b""))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("app.cv.parser.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setenv("AGENT_MODEL_API_KEY", "private-test-key")
    with pytest.raises(CVError, match="invalid_parser_evidence"):
        await parse_pdf(b"%PDF-1.7")
    assert "AGENT_MODEL_API_KEY" not in spawn.call_args.kwargs["env"]


def test_matching_hand_scores_gap_evidence_and_content_dedup() -> None:
    profile = extract_skills([CVPage(number=1, text="Python project experience")], [])
    python = SourceSkillEvidence(
        slug="python", source_field="description", start=0, end=6, text="Python"
    )
    docker = SourceSkillEvidence(
        slug="docker", source_field="description", start=7, end=13, text="Docker"
    )
    one = replace(
        document("Python Docker", identity=1),
        skills=("python", "docker"),
        skill_evidence=(python, docker),
    )
    duplicate = replace(one, job_id=uuid.UUID(int=2), source_job_id="2")
    other = replace(document("Java", identity=3), skills=())
    report = match_jobs(
        uuid.uuid4(), datetime.now(UTC), profile, MatchRequest(), [one, duplicate, other]
    )
    assert report.eligible_listings == 3 and report.unique_documents == 2
    result = report.items[0]
    assert result.job_id == one.job_id and result.duplicate_listings == 2
    assert result.skill_coverage == 0.5
    assert result.score == pytest.approx(0.7 * 0.5 + 0.3)
    assert [s.slug for s in result.matched_skills] == ["python"]
    assert [s.slug for s in result.skills_not_seen_in_cv] == ["docker"]
    assert result.skills_not_seen_in_cv[0].job_evidence.text == "Docker"


def test_requirement_levels_weight_coverage_order_gaps_and_drop_negated() -> None:
    profile = extract_skills([CVPage(number=1, text="Python project experience")], [])

    def evidence(slug: str, start: int, requirement: str) -> SourceSkillEvidence:
        return SourceSkillEvidence(
            slug=slug,
            source_field="description",
            start=start,
            end=start + 1,
            text=slug,
            requirement=requirement,  # type: ignore[arg-type]
        )

    items = (
        evidence("python", 0, "required"),
        evidence("react", 1, "preferred"),
        evidence("docker", 2, "required"),
        evidence("java", 3, "negated"),
    )
    doc = replace(
        document("Python", identity=1),
        skills=tuple(item.slug for item in items),
        skill_evidence=items,
    )
    result = match_jobs(uuid.uuid4(), datetime.now(UTC), profile, MatchRequest(), [doc]).items[0]
    # python (1.0) of python + docker (1.0 each) + react (0.5); java is not a job skill.
    assert result.skill_coverage == pytest.approx(1.0 / 2.5)
    assert [(s.slug, s.requirement) for s in result.skills_not_seen_in_cv] == [
        ("docker", "required"),
        ("react", "preferred"),
    ]
    assert result.negated_job_skills == ["java"]


def test_unknown_skills_empty_pool_and_no_evidence_are_not_false_gaps() -> None:
    profile = extract_skills([CVPage(number=1, text="Unrecognized technology fixture")], [])
    doc = document("Python project")
    empty = match_jobs(uuid.uuid4(), datetime.now(UTC), profile, MatchRequest(), [doc])
    assert empty.items == []
    result = match_jobs(
        uuid.uuid4(), datetime.now(UTC), profile, MatchRequest(target_role="Python"), [doc]
    )
    assert len(result.items) == 1
    assert result.items[0].skill_coverage is None
    assert result.items[0].skills_not_seen_in_cv == []


@pytest.mark.parametrize(
    "headers,data,status",
    [
        ({"content-type": "application/octet-stream"}, b"notpdf", 415),
        ({"content-type": "application/pdf"}, b"notpdf", 422),
        ({"content-type": "application/pdf", "content-length": str(MAX_PDF_BYTES + 1)}, b"", 413),
        ({"content-type": "application/pdf", "content-length": "bad"}, b"", 400),
    ],
)
def test_upload_envelope_rejected_without_database(
    settings: Settings, headers: dict[str, str], data: bytes, status: int
) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/cv", headers=headers, content=data)
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/cv/" + str(uuid.uuid4())).status_code == 404


def test_stream_limit_without_content_length_and_parser_busy(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/cv",
            headers={"content-type": "application/pdf"},
            content=iter([b"%PDF-", b"a" * MAX_PDF_BYTES]),
        )
        assert response.status_code == 413
        client.app.state.cv_service.upload_slots = asyncio.Semaphore(0)
        response = client.post("/cv", headers={"content-type": "application/pdf"}, content=b"%PDF-")
        assert response.status_code == 503
