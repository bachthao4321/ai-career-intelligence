from typing import Any

import pytest

from app.cli import refresh as module
from app.core.config import Settings


def test_parse_sources_accepts_public_subsets_only() -> None:
    assert module.parse_sources("jobtech, employers,jobtech") == ("jobtech", "employers")
    for bad in ("", "itviec", "jobtech,itviec", " , "):
        with pytest.raises(ValueError):
            module.parse_sources(bad)


async def test_refresh_rejects_aggressive_polling(settings: Settings) -> None:
    with pytest.raises(ValueError, match="at least 900"):
        await module.refresh(settings, ("jobtech",), every=60)


async def test_one_failing_source_does_not_block_others(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Engine:
        async def dispose(self) -> None:
            pass

    async def import_source(engine: Any, config: Settings, source: str) -> dict[str, Any]:
        if source == "jobtech":
            raise OSError("source down")
        return {"valid": 1, "inserted": 1}

    async def update_index(engine: Any, config: Settings) -> dict[str, Any]:
        return {"indexed": 1}

    monkeypatch.setattr(module, "create_engine", lambda config: Engine())
    monkeypatch.setattr(module, "import_source", import_source)
    monkeypatch.setattr(module, "update_index", update_index)
    report = await module.refresh(settings, ("jobtech", "employers"), every=None)
    assert report["sources"] == {
        "jobtech": {"error": "OSError"},
        "employers": {"valid": 1, "inserted": 1},
    }
    assert report["index"] == {"indexed": 1}
