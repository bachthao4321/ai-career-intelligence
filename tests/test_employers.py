import json

import pytest

from app.ingestion import employers


def page(job_data: dict[str, object]) -> str:
    data = {"props": {"pageProps": {"job_data": job_data}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_vng_detail_keeps_structure_and_titles_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = page(
        {
            "description": (
                "<p><strong>About us</strong></p><ul><li>Build APIs</li><li>Run Kafka</li></ul>"
            ),
            "requirement": "<ul><li>3 years of Go</li></ul>",
        }
    )
    monkeypatch.setattr(employers, "get", lambda url: html)
    text = employers.vng_detail("123-role-vi")
    assert text is not None
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines == ["About us", "Build APIs", "Run Kafka", "Yêu cầu công việc", "3 years of Go"]


def test_vng_detail_without_page_data_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(employers, "get", lambda url: "<html>maintenance</html>")
    assert employers.vng_detail("x") is None
    monkeypatch.setattr(employers, "get", lambda url: page({}))
    assert employers.vng_detail("x") is None
