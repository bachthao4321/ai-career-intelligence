"""In-memory synthetic PDF fixtures; no real identity or CV data."""

import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

SYNTHETIC_CV = (
    "SYNTHETIC CV - TEST DATA ONLY\n"
    "Backend developer project experience\n"
    "Skills: Python, Postgres\n"
    "Built database-backed services in a personal project."
)


def pdf_bytes(
    pages: list[str] | None = None,
    *,
    password: str | None = None,
    compressed_content: bytes | None = None,
) -> bytes:
    writer = PdfWriter()
    for value in pages if pages is not None else [SYNTHETIC_CV]:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        commands = ["BT /F1 11 Tf 40 740 Td"]
        for line in value.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands += [f"({escaped}) Tj", "0 -16 Td"]
        commands.append("ET")
        stream.set_data(
            compressed_content
            if compressed_content is not None
            else "\n".join(commands).encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(
            stream.flate_encode() if compressed_content is not None else stream
        )
    if password is not None:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
