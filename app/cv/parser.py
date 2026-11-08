import asyncio
import json
import sys

from app.cv.contracts import (
    MAX_PDF_BYTES,
    MAX_TEXT_CHARACTERS,
    PARSER_TIMEOUT_SECONDS,
    CVError,
    CVProfile,
)
from app.ingestion.contracts import alias_key
from app.ingestion.extraction import SkillCatalog


async def parse_pdf(data: bytes) -> CVProfile:
    if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
        raise CVError("invalid_or_oversize_pdf")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cv.worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(data), PARSER_TIMEOUT_SECONDS)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if sys.exc_info()[0] is TimeoutError:
            raise CVError("pdf_parser_timeout") from None
        raise
    if process.returncode != 0 or len(stdout) > 512000:
        raise CVError("pdf_parser_resource_limit")
    try:
        body = json.loads(stdout)
        if body.get("error"):
            raise CVError(body["error"])
        profile = CVProfile.model_validate(body["profile"])
        # Validate evidence again at the parent trust boundary.
        texts = {page.number: page.text for page in profile.pages}
        if (
            list(texts) != list(range(1, len(profile.pages) + 1))
            or sum(len(page.text) for page in profile.pages) > MAX_TEXT_CHARACTERS
        ):
            raise CVError("invalid_parser_pages")
        catalog = SkillCatalog.load()
        for skill in profile.skills:
            for item in skill.evidence:
                if (
                    item.page not in texts
                    or item.end > len(texts[item.page])
                    or texts[item.page][item.start : item.end] != item.text
                    or catalog.aliases.get(alias_key(item.text)) != skill.slug
                ):
                    raise CVError("invalid_parser_evidence")
        return profile
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, CVError):
            raise
        raise CVError("invalid_pdf_parser_output") from None
