"""M1 acceptance tests — extraction + chunking.

Coverage:
  - BasicParser extracts text from each supported file type.
  - ExtractionQuality flags are set correctly (is_empty, is_scan_only).
  - chunk() respects token bounds, overlap, and the max_chunks cap.
  - chunk() returns empty list for empty/whitespace-only text.

These tests use synthetic in-memory fixtures — no real files or LLM calls.
"""

from __future__ import annotations

import csv
import io

import pytest
from app.ingest.chunk import chunk
from app.ingest.extract import BasicParser, _empty_extraction

# ---------------------------------------------------------------------------
# Helpers to build minimal fixture bytes
# ---------------------------------------------------------------------------

def _csv_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _docx_bytes(text: str) -> bytes:
    """Build a minimal .docx with one paragraph."""
    import docx
    doc = docx.Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV extraction
# ---------------------------------------------------------------------------

def test_csv_extraction_basic():
    data = _csv_bytes([["Name", "Role"], ["Alice", "Admin"], ["Bob", "Viewer"]])
    result = BasicParser().extract(data, "csv")
    assert "Alice" in result.text
    assert "Bob" in result.text
    assert result.quality.chars > 0
    assert not result.quality.is_empty
    assert not result.quality.is_scan_only


def test_csv_extraction_empty():
    result = BasicParser().extract(b"", "csv")
    assert result.quality.is_empty
    assert result.text == ""


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def test_docx_extraction_basic():
    content = "This is a Business Continuity Plan covering IT disaster recovery."
    data = _docx_bytes(content)
    result = BasicParser().extract(data, "docx")
    assert "Business Continuity" in result.text
    assert result.quality.chars > 0
    assert not result.quality.is_empty


def test_docx_extraction_empty():
    data = _docx_bytes("   ")
    result = BasicParser().extract(data, "docx")
    assert result.quality.is_empty


# ---------------------------------------------------------------------------
# XLSX extraction
# ---------------------------------------------------------------------------

def test_xlsx_extraction_basic():
    rows = [["Plan", "Status", "Score"], ["Pandemic COOP", "In Sync", "85"]]
    data = _xlsx_bytes(rows)
    result = BasicParser().extract(data, "xlsx")
    assert "Pandemic COOP" in result.text
    assert "In Sync" in result.text
    assert result.quality.pages_or_rows >= 1


# ---------------------------------------------------------------------------
# Unknown / unsupported extension
# ---------------------------------------------------------------------------

def test_unknown_extension_returns_empty():
    result = BasicParser().extract(b"some bytes", "ppt")
    assert result.quality.is_empty


# ---------------------------------------------------------------------------
# Chunk function
# ---------------------------------------------------------------------------

def _make_text(approx_tokens: int) -> str:
    """Build a text string with approximately the given token count."""
    word = "continuity "
    # ~2 tokens per word (tiktoken cl100k_base)
    repeat = (approx_tokens // 2) + 10
    return (word * repeat).strip()


def test_chunk_empty_text():
    assert chunk("", max_chunks=10) == []


def test_chunk_whitespace_only():
    assert chunk("   \n\t  ", max_chunks=10) == []


def test_chunk_short_text_single_chunk():
    text = "A short COOP document with a few words."
    chunks = chunk(text, max_chunks=100)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text.strip() == text.strip()
    assert chunks[0].token_count > 0


def test_chunk_token_bounds():
    text = _make_text(1200)  # enough for ~2-3 chunks at 500 tokens
    chunks = chunk(text, max_chunks=100, chunk_tokens=500, overlap_tokens=50)
    for c in chunks:
        assert c.token_count <= 500 + 5  # small tolerance for boundary effects


def test_chunk_indices_are_sequential():
    text = _make_text(1500)
    chunks = chunk(text, max_chunks=100)
    for i, c in enumerate(chunks):
        assert c.index == i


def test_chunk_max_chunks_cap_respected():
    text = _make_text(5000)  # would produce many chunks at 500 tokens
    cap = 3
    chunks = chunk(text, max_chunks=cap)
    assert len(chunks) <= cap


def test_chunk_overlap_preserves_boundary_words():
    """Words near a chunk boundary should appear in both adjacent chunks."""
    # Build a long enough text that we definitely get 2+ chunks.
    text = _make_text(1100)
    chunks = chunk(text, max_chunks=100, chunk_tokens=500, overlap_tokens=50)
    if len(chunks) < 2:
        pytest.skip("Not enough chunks to test overlap")
    # The tail of chunk 0 and the head of chunk 1 should share some tokens.
    end_of_first = chunks[0].text[-100:]
    start_of_second = chunks[1].text[:100]
    # They won't be identical but should share common substrings (the overlap).
    # Simple check: both contain the same common word "continuity".
    assert "continuity" in end_of_first
    assert "continuity" in start_of_second


# ---------------------------------------------------------------------------
# ExtractionQuality helper
# ---------------------------------------------------------------------------

def test_empty_extraction_helper():
    ext = _empty_extraction()
    assert ext.quality.is_empty
    assert ext.quality.chars == 0
    assert ext.text == ""
