"""PDF loader implementation using MarkItDown for canonical Markdown output.

Replaces the legacy pypdf text extraction with MarkItDown to produce
structured Markdown (headings, lists, code blocks) aligned with DEV_SPEC.
Image extraction falls back to pypdf when available.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.types import Document, format_image_placeholder, validate_document_contract

from .base_loader import BaseLoader

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE_ROOT = REPO_ROOT / "data" / "images"

# Match Markdown image syntax: ![alt](path)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class PdfLoader(BaseLoader):
    """Load PDF into a Document with canonical Markdown text and optional image extraction.

    Uses MarkItDown as the primary conversion engine (per DEV_SPEC requirement).
    Image extraction degrades gracefully: Markdown ``![](ref)`` references are
    normalised to ``[IMAGE: {id}]`` placeholders, and pypdf is used as a secondary
    engine to extract embedded image bytes when available.
    """

    def __init__(
        self,
        image_root: Optional[str] = None,
        enable_image_extraction: bool = True,
        converter: Optional[Callable[[str], str]] = None,
    ):
        """Initialise PdfLoader.

        Args:
            image_root: Directory to store extracted image files.
            enable_image_extraction: When False, skip all image extraction.
            converter: Optional callable ``(file_path: str) -> str`` that returns
                Markdown text.  Injected by tests; when omitted MarkItDown is used.
        """
        self.image_root = Path(image_root) if image_root else DEFAULT_IMAGE_ROOT
        self.enable_image_extraction = enable_image_extraction
        self._converter = converter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: str, trace: Optional[Any] = None) -> Document:
        """Load a PDF file and return a canonical-Markdown Document.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ImportError: If MarkItDown is not installed and no *converter*
                was supplied.
        """
        fp = Path(path)
        if not fp.is_file():
            raise FileNotFoundError(f"PDF file not found: {fp}")

        doc_hash = self._compute_file_hash(fp)

        # ── 1. Convert PDF → canonical Markdown ──────────────────────
        markdown_text = self._convert_to_markdown(str(fp))

        # ── 2. Normalise Markdown image refs (![]() → [IMAGE:xxx]) ──
        images: List[Dict[str, Any]] = []
        markdown_text, md_images = self._normalise_markdown_image_refs(
            markdown_text, doc_hash
        )
        images.extend(md_images)

        # ── 3. Extract embedded images via pypdf (secondary engine) ──
        page_count: Optional[int] = None
        if self.enable_image_extraction:
            try:
                pdf_images, page_count = self._extract_images_via_pypdf(fp, doc_hash)
            except ImportError:
                logger.debug(
                    "pypdf not available – skipping embedded-image extraction"
                )
            except Exception as exc:  # noqa: BLE001 – degrade by design
                logger.warning("pypdf image extraction failed: %s", exc)
            else:
                existing_ids = {img["id"] for img in images}
                for img_meta in pdf_images:
                    if img_meta["id"] not in existing_ids:
                        placeholder = format_image_placeholder(img_meta["id"])
                        # Append placeholder at end of text (page-level mapping
                        # is not feasible with MarkItDown's monolithic output).
                        if markdown_text and not markdown_text.endswith("\n"):
                            markdown_text += "\n"
                        img_meta["text_offset"] = len(markdown_text)
                        img_meta["text_length"] = len(placeholder)
                        markdown_text += placeholder
                        images.append(img_meta)

        # ── 4. Assemble Document ─────────────────────────────────────
        metadata: Dict[str, Any] = {
            "source_path": str(fp.resolve()),
            "doc_type": "pdf",
        }
        if page_count is not None:
            metadata["page_count"] = page_count
        if images:
            metadata["images"] = images

        document = Document(
            id=doc_hash,
            text=markdown_text.strip(),
            metadata=metadata,
        )
        validate_document_contract(document)
        return document

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _convert_to_markdown(self, path: str) -> str:
        """Return canonical Markdown for *path* using MarkItDown or an
        injected *converter* callable."""
        if self._converter is not None:
            return self._converter(path)

        try:
            from markitdown import MarkItDown  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "PDF loader requires 'markitdown'. "
                "Install it with: pip install markitdown"
            ) from exc

        converter = MarkItDown()
        result = converter.convert(path)
        return result.text_content

    @staticmethod
    def _normalise_markdown_image_refs(
        text: str, doc_hash: str
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Replace ``![alt](ref)`` Markdown image syntax with
        ``[IMAGE: {image_id}]`` placeholders.

        Returns:
            ``(normalised_text, image_metadata_list)``
        """
        images: List[Dict[str, Any]] = []
        seen_ids: Dict[str, int] = {}

        def _replacer(match: re.Match) -> str:
            alt = match.group(1) or "image"
            ref = match.group(2)
            # Deduplicate by ref path
            idx = seen_ids.get(ref, 0)
            seen_ids[ref] = idx + 1
            image_id = f"{doc_hash}_md_{hashlib.md5(ref.encode()).hexdigest()[:8]}_{idx}"

            placeholder = format_image_placeholder(image_id)

            images.append(
                {
                    "id": image_id,
                    "path": ref,
                    "text_offset": -1,  # filled later by caller
                    "text_length": len(placeholder),
                    "position": {},
                    "source": "markitdown",
                }
            )
            return placeholder

        normalised = _MARKDOWN_IMAGE_RE.sub(_replacer, text)

        # Back-fill text offsets after all replacements
        for img in images:
            placeholder = format_image_placeholder(img["id"])
            offset = normalised.find(placeholder)
            if offset >= 0:
                img["text_offset"] = offset
            else:
                img["text_offset"] = 0  # defensive fallback

        return normalised, images

    # ------------------------------------------------------------------
    # Image extraction via pypdf (secondary engine)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_images_via_pypdf(
        fp: Path, doc_hash: str
    ) -> tuple[List[Dict[str, Any]], int]:
        """Extract embedded image bytes from PDF using pypdf.

        Returns:
            ``(image_metadata_list, page_count)``

        Raises:
            ImportError: If pypdf (or PyPDF2) is not installed.
        """
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ImportError(
                    "Embedded-image extraction requires 'pypdf' (preferred) or "
                    "'PyPDF2'. Install with: pip install pypdf"
                ) from exc

        reader = PdfReader(str(fp))
        page_count = len(reader.pages)

        output_dir = DEFAULT_IMAGE_ROOT / doc_hash
        output_dir.mkdir(parents=True, exist_ok=True)

        images: List[Dict[str, Any]] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            images_obj = getattr(page, "images", None)
            if not images_obj:
                continue

            for index, img in enumerate(images_obj):
                image_id = f"{doc_hash}_{page_idx}_{index}"
                data = getattr(img, "data", b"")
                if not isinstance(data, (bytes, bytearray)) or not data:
                    continue

                ext = Path(getattr(img, "name", "")).suffix or ".png"
                file_name = f"{image_id}{ext}"
                file_path = output_dir / file_name
                file_path.write_bytes(data)

                images.append(
                    {
                        "id": image_id,
                        "path": _to_repo_relative(file_path),
                        "page": page_idx,
                        "position": {},
                        "source": "pypdf",
                    }
                )

        return images, page_count

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_file_hash(path: Path) -> str:
        """SHA-256 hash of file content (streamed)."""
        h = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()


def _to_repo_relative(path: Path) -> str:
    """Convert absolute *path* to repo-relative if possible."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
