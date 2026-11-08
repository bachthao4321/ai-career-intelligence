"""One-shot PDF worker. No credentials, network calls, files or raw diagnostics."""

import io
import json
import logging
import sys
import warnings


def set_resource_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (4, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def parse_pdf(data: bytes) -> dict[str, object]:
    from pypdf import PdfReader, overwrite_configuration

    from app.cv.contracts import (
        MAX_CONTENT_BYTES,
        MAX_PAGES,
        MAX_PDF_BYTES,
        MAX_TEXT_CHARACTERS,
        CVError,
        CVPage,
    )
    from app.cv.extraction import extract_skills
    from app.ingestion.contracts import normalize_text

    if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
        raise CVError("invalid_or_oversize_pdf")
    # Public pypdf 7 configuration API, local to this one-shot execution context.
    overwrite_configuration(
        maximum_declared_stream_length=MAX_CONTENT_BYTES,
        array_based_stream_maximum_output_length=MAX_CONTENT_BYTES,
        jbig2_maximum_output_length=MAX_CONTENT_BYTES,
        lzw_maximum_output_length=MAX_CONTENT_BYTES,
        run_length_maximum_output_length=MAX_CONTENT_BYTES,
        zlib_maximum_output_length=MAX_CONTENT_BYTES,
        brotli_maximum_output_length=MAX_CONTENT_BYTES,
        stream_decoding_work_maximum_length=2 * MAX_CONTENT_BYTES,
        stream_parsing_work_maximum_count=100000,
        page_tree_maximum_entries=1000,
        page_tree_maximum_depth=32,
        xform_maximum_invocations_per_extraction=100,
        jbig2dec_binary=None,
    )
    reader = PdfReader(
        io.BytesIO(data),
        strict=True,
        root_object_recovery_limit=1000,
        xref_stream_maximum_entries=100000,
    )
    if reader.is_encrypted:
        raise CVError("encrypted_pdf_not_supported")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise CVError("pdf_page_limit")
    pages = []
    total_content = total_chars = 0
    blank = False
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        total_content += len(contents.get_data()) if contents else 0
        if total_content > MAX_CONTENT_BYTES:
            raise CVError("pdf_content_limit")
        value = normalize_text((page.extract_text() or "").replace("\x00", ""))
        total_chars += len(value)
        if total_chars > MAX_TEXT_CHARACTERS:
            raise CVError("pdf_text_limit")
        blank = blank or not value.strip()
        pages.append(CVPage(number=number, text=value))
    if sum(sum(c.isalnum() for c in page.text) for page in pages) < 20:
        raise CVError("no_extractable_text_ocr_not_supported")
    profile = extract_skills(
        pages, ["some_pages_without_text_report_may_be_incomplete"] if blank else []
    )
    return profile.model_dump(mode="json")


def main() -> None:
    result: dict[str, object]
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    try:
        set_resource_limits()
        from app.cv.contracts import MAX_PDF_BYTES, CVError

        data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        try:
            result = {"profile": parse_pdf(data)}
        except CVError as exc:
            result = {"error": str(exc)}
    except Exception:
        result = {"error": "invalid_pdf_or_parser_resource_limit"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
