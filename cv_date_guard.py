"""Minimal immutable-date patch for the generated CV DOCX.

This module changes ONLY the literal current-role end date inside
word/document.xml. It does not open or resave the document with python-docx,
so paragraph styles, fonts, runs, pagination, section settings and all
historical roles remain untouched.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path


CURRENT_ROLE_START = b"Aug 2024"
LEGACY_END_DATE = b"Feb 2026"
REAL_END_DATE = b"Jun 2026"

_PARAGRAPH_RE = re.compile(rb"<w:p(?:\s[^>]*)?>.*?</w:p>", re.DOTALL)
_TEXT_NODE_RE = re.compile(
    rb"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)",
    re.DOTALL,
)


def _joined_text(paragraph_xml: bytes) -> bytes:
    return b"".join(match.group(2) for match in _TEXT_NODE_RE.finditer(paragraph_xml))


def _replace_date_in_paragraph(paragraph_xml: bytes) -> tuple[bytes, int]:
    """Replace Feb 2026 with Jun 2026 across split w:t nodes.

    Both strings have the same byte length, so text is redistributed over the
    exact same XML text nodes. No run or paragraph markup is rebuilt.
    """
    matches = list(_TEXT_NODE_RE.finditer(paragraph_xml))
    if not matches:
        return paragraph_xml, 0

    joined = b"".join(match.group(2) for match in matches)
    if CURRENT_ROLE_START not in joined:
        return paragraph_xml, 0
    if REAL_END_DATE in joined:
        return paragraph_xml, 0
    if LEGACY_END_DATE not in joined:
        return paragraph_xml, 0

    replaced = joined.replace(LEGACY_END_DATE, REAL_END_DATE)
    if len(replaced) != len(joined):
        raise RuntimeError("CV date replacement unexpectedly changed XML text length.")

    rebuilt = bytearray()
    last = 0
    offset = 0
    for match in matches:
        rebuilt.extend(paragraph_xml[last:match.start(2)])
        original_text = match.group(2)
        length = len(original_text)
        rebuilt.extend(replaced[offset:offset + length])
        offset += length
        last = match.end(2)
    rebuilt.extend(paragraph_xml[last:])
    return bytes(rebuilt), joined.count(LEGACY_END_DATE)


def _patch_document_xml(document_xml: bytes) -> tuple[bytes, int]:
    replacements = 0
    rebuilt = bytearray()
    last = 0

    for match in _PARAGRAPH_RE.finditer(document_xml):
        paragraph = match.group(0)
        patched, count = _replace_date_in_paragraph(paragraph)
        rebuilt.extend(document_xml[last:match.start()])
        rebuilt.extend(patched)
        replacements += count
        last = match.end()

    rebuilt.extend(document_xml[last:])
    patched_xml = bytes(rebuilt)

    current_role_paragraphs = [
        _joined_text(match.group(0))
        for match in _PARAGRAPH_RE.finditer(patched_xml)
        if CURRENT_ROLE_START in _joined_text(match.group(0))
    ]
    if not any(REAL_END_DATE in text for text in current_role_paragraphs):
        raise RuntimeError(
            "No se encontró Aug 2024 – Jun 2026 en el DOCX generado después del guardarraíl."
        )
    if any(LEGACY_END_DATE in text for text in current_role_paragraphs):
        raise RuntimeError("CV date guard failed; Feb 2026 remains in the current role.")

    return patched_xml, replacements


def enforce_real_current_date(docx_path: str) -> int:
    """Patch only word/document.xml in place and preserve all formatting XML."""
    path = Path(docx_path)
    if not path.exists():
        raise FileNotFoundError(path)

    with zipfile.ZipFile(path, "r") as source:
        document_xml = source.read("word/document.xml")
        patched_xml, replacements = _patch_document_xml(document_xml)

        if patched_xml == document_xml:
            return replacements

        fd, tmp_name = tempfile.mkstemp(
            prefix=path.stem + "_date_",
            suffix=".docx",
            dir=str(path.parent),
        )
        os.close(fd)

        try:
            with zipfile.ZipFile(tmp_name, "w") as target:
                for info in source.infolist():
                    data = patched_xml if info.filename == "word/document.xml" else source.read(info.filename)
                    target.writestr(info, data)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    return replacements
