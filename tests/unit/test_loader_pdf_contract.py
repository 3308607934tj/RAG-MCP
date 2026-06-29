from __future__ import annotations

from pathlib import Path

import pytest

from src.core import IMAGE_PLACEHOLDER_TEMPLATE
from src.libs.loader import BaseLoader, PdfLoader


# ── Fake converter for tests (replaces _FakeReader/_FakePage) ──────
def _fake_converter(path: str) -> str:
    """Return a fixed Markdown string, simulating MarkItDown output."""
    return "## Section\n\nHello world\n\n![img](images/img1.png)"


def test_base_loader_is_abstract():
    with pytest.raises(TypeError):
        BaseLoader()  # type: ignore[abstract]


def test_pdf_loader_load_minimal_pdf(tmp_path: Path):
    pdf_path = tmp_path / "simple.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    loader = PdfLoader(
        image_root=str(tmp_path / "images"),
        enable_image_extraction=False,
        converter=lambda _: "## Heading\n\nHello from MarkItDown",
    )
    doc = loader.load(str(pdf_path))

    assert doc.id
    assert doc.metadata["source_path"].endswith("simple.pdf")
    assert doc.metadata["doc_type"] == "pdf"
    assert "## Heading" in doc.text
    assert "Hello from MarkItDown" in doc.text
    assert "images" not in doc.metadata or doc.metadata["images"] == []


def test_pdf_loader_missing_file_raises(tmp_path: Path):
    loader = PdfLoader(image_root=str(tmp_path / "images"))
    with pytest.raises(FileNotFoundError):
        loader.load(str(tmp_path / "missing.pdf"))


def test_pdf_loader_inserts_image_placeholders_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Markdown ![](ref) images are converted to [IMAGE:xxx] placeholders."""
    pdf_path = tmp_path / "with_images.pdf"
    pdf_path.write_bytes(b"%PDF-fake-with-images")

    loader = PdfLoader(
        image_root=str(tmp_path / "images"),
        enable_image_extraction=False,
        converter=_fake_converter,
    )
    doc = loader.load(str(pdf_path))

    # The Markdown ![](images/img1.png) should become [IMAGE: ...]
    assert "[IMAGE:" in doc.text
    assert "images" in doc.metadata
    assert len(doc.metadata["images"]) >= 1

    img = doc.metadata["images"][0]
    assert img["id"]
    assert img["text_length"] > 0
    placeholder = f"[IMAGE: {img['id']}]"
    assert placeholder in doc.text
    # Verify text_offset points to the correct position
    assert doc.text[img["text_offset"] : img["text_offset"] + img["text_length"]] == placeholder


def test_pdf_loader_pypdf_images_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """pypdf-extracted images are appended as placeholders at end of text."""
    pdf_path = tmp_path / "pypdf_img.pdf"
    pdf_path.write_bytes(b"%PDF-fake-pypdf-images")

    fake_id = "abcd1234_1_0"

    def _fake_extract(fp, doc_hash: str):
        return (
            [
                {
                    "id": fake_id,
                    "path": f"data/images/{doc_hash}/{fake_id}.png",
                    "page": 1,
                    "position": {"x": 10, "y": 20},
                }
            ],
            5,  # page_count
        )

    loader = PdfLoader(
        image_root=str(tmp_path / "images"),
        converter=lambda _: "# Doc\n\nSome text here",
    )
    monkeypatch.setattr(loader, "_extract_images_via_pypdf", _fake_extract)
    doc = loader.load(str(pdf_path))

    placeholder = IMAGE_PLACEHOLDER_TEMPLATE.format(image_id=fake_id)
    assert placeholder in doc.text
    assert doc.metadata["page_count"] == 5
    assert "images" in doc.metadata
    assert len(doc.metadata["images"]) == 1
    img = doc.metadata["images"][0]
    assert img["id"] == fake_id
    assert img["page"] == 1
    assert img["text_length"] == len(placeholder)
    assert doc.text[img["text_offset"] : img["text_offset"] + img["text_length"]] == placeholder


def test_pdf_loader_image_extract_error_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Image extraction failure must not crash loading."""
    pdf_path = tmp_path / "degrade.pdf"
    pdf_path.write_bytes(b"%PDF-fake-degrade")

    def _boom(fp, doc_hash: str):
        raise RuntimeError("image parse failed")

    loader = PdfLoader(
        image_root=str(tmp_path / "images"),
        converter=lambda _: "# Title\n\nBase content",
    )
    monkeypatch.setattr(loader, "_extract_images_via_pypdf", _boom)
    doc = loader.load(str(pdf_path))

    assert doc.metadata["source_path"].endswith("degrade.pdf")
    assert "# Title" in doc.text
    # Image extraction failure → no images key
    assert "images" not in doc.metadata


def test_pdf_loader_requires_markitdown_when_no_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """ImportError with 'markitdown' message when converter is None and
    markitdown is not installed (simulated via import mock)."""
    pdf_path = tmp_path / "dep.pdf"
    pdf_path.write_bytes(b"%PDF-fake-dependency")

    loader = PdfLoader(image_root=str(tmp_path / "images"))

    import builtins
    original_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "markitdown" or name == "markitdown._markitdown":
            raise ImportError("No module named 'markitdown'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)
    with pytest.raises(ImportError, match="markitdown"):
        loader.load(str(pdf_path))
