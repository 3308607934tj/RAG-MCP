"""Pre-ingestion document quality gate (Layer-1 defence).

Samples the first few pages of a PDF via pypdf and computes a
valid-character ratio together with text density.  Documents whose
metrics fall below configurable thresholds are rejected outright,
preventing garbage chunks from entering the pipeline.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.settings import QualityCheckSettings

logger = logging.getLogger(__name__)

# Defaults are applied when pipeline settings lack an explicit quality_check block.
_DEFAULT_ENABLED = True
_DEFAULT_SAMPLE_PAGES = 3
_DEFAULT_MIN_VALID_CHAR_RATIO = 0.25
_DEFAULT_MIN_TEXT_DENSITY = 0.1

# ---------------------------------------------------------------------------
# Unicode helpers
# ---------------------------------------------------------------------------

# CJK + CJK-related blocks used in technical PDFs (Chinese / Japanese / Korean)
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0x3000, 0x303F),   # CJK Symbols & Punctuation
    (0xFF00, 0xFFEF),   # Halfwidth & Fullwidth Forms
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x31C0, 0x31EF),   # CJK Strokes
]

_REPLACEMENT_CHAR = "�"

# A "meaningful" line must have at least this many word-chars
_MIN_WORD_CHARS_PER_LINE = 3


def _is_cjk(cp: int) -> bool:
    """True when *cp* falls inside a known CJK Unicode block."""
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_valid_char(ch: str) -> bool:
    """Return True for characters that signal genuine document content."""
    if ch == _REPLACEMENT_CHAR:
        return False  # explicit U+FFFD replacement character
    cat = unicodedata.category(ch)
    # Control (Cc) except the three common line/indent controls
    if cat == "Cc":
        return ch in ("\t", "\n", "\r")
    # Private-use, surrogates, unassigned
    if cat in ("Co", "Cs", "Cn"):
        return False
    return True


def _is_word_char(ch: str) -> bool:
    """Does *ch* represent a letter, digit, or CJK ideograph?"""
    cp = ord(ch)
    if ch.isalnum():
        return True
    if _is_cjk(cp):
        return True
    return False


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityCheckResult:
    """Immutable quality-check outcome."""

    passed: bool
    valid_char_ratio: float
    text_density: float
    sampled_pages: int
    total_chars: int
    valid_chars: int
    total_lines: int
    meaningful_lines: int
    reason: str = ""


class DocumentRejectedError(RuntimeError):
    """Raised when a document fails the pre-ingestion quality gate."""

    def __init__(self, file_path: str, result: QualityCheckResult):
        self.file_path = file_path
        self.result = result
        msg = (
            f"Document rejected by quality gate: {file_path} — "
            f"valid_char_ratio={result.valid_char_ratio:.3f} "
            f"(min={_DEFAULT_MIN_VALID_CHAR_RATIO:.2f}), "
            f"text_density={result.text_density:.3f}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Quality checker
# ---------------------------------------------------------------------------


class DocumentQualityChecker:
    """Sample first N pages of a PDF via pypdf and validate text quality.

    Uses the same pypdf backend as the image-extraction path so no extra
    dependency is introduced.  Completely self-contained — this is the
    *only* quality gate before the Loader stage.
    """

    def __init__(
        self,
        settings: Optional[QualityCheckSettings] = None,
    ) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # properties derived from settings or defaults
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        if self._settings is not None:
            return self._settings.enabled
        return _DEFAULT_ENABLED

    @property
    def _sample_pages(self) -> int:
        if self._settings is not None:
            return self._settings.sample_pages
        return _DEFAULT_SAMPLE_PAGES

    @property
    def _min_valid_char_ratio(self) -> float:
        if self._settings is not None:
            return self._settings.min_valid_char_ratio
        return _DEFAULT_MIN_VALID_CHAR_RATIO

    @property
    def _min_text_density(self) -> float:
        if self._settings is not None:
            return self._settings.min_text_density
        return _DEFAULT_MIN_TEXT_DENSITY

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def check(self, file_path: str) -> QualityCheckResult:
        """Run the quality gate on *file_path*.

        Returns a *QualityCheckResult* with ``passed=True`` when the
        document meets the configured thresholds.

        Raises *DocumentRejectedError* when ``passed=False`` — callers
        should catch this to either skip or log the rejection.
        """
        if not self.enabled:
            return QualityCheckResult(
                passed=True,
                valid_char_ratio=1.0,
                text_density=1.0,
                sampled_pages=0,
                total_chars=0,
                valid_chars=0,
                total_lines=0,
                meaningful_lines=0,
                reason="quality_check disabled",
            )

        result = self._sample_and_evaluate(file_path)
        if not result.passed:
            raise DocumentRejectedError(file_path, result)
        return result

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _sample_and_evaluate(self, file_path: str) -> QualityCheckResult:
        """Extract text from the first N pages and compute metrics."""
        text = self._extract_sample_text(file_path, self._sample_pages)

        total_chars = 0
        valid_chars = 0
        for ch in text:
            total_chars += 1
            if _is_valid_char(ch):
                valid_chars += 1

        valid_char_ratio = (valid_chars / total_chars) if total_chars > 0 else 0.0

        # Text density: how many lines carry actual word-level content
        lines = text.splitlines()
        total_lines = 0
        meaningful_lines = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            total_lines += 1
            word_chars = sum(1 for ch in stripped if _is_word_char(ch))
            if word_chars >= _MIN_WORD_CHARS_PER_LINE:
                meaningful_lines += 1

        text_density = (meaningful_lines / total_lines) if total_lines > 0 else 0.0

        reason = ""
        if valid_char_ratio < self._min_valid_char_ratio:
            reason = (
                f"valid_char_ratio={valid_char_ratio:.3f} "
                f"< threshold={self._min_valid_char_ratio:.2f}"
            )
        elif text_density < self._min_text_density:
            reason = (
                f"text_density={text_density:.3f} "
                f"< threshold={self._min_text_density:.2f}"
            )

        passed = (valid_char_ratio >= self._min_valid_char_ratio
                  and text_density >= self._min_text_density)

        return QualityCheckResult(
            passed=passed,
            valid_char_ratio=valid_char_ratio,
            text_density=text_density,
            sampled_pages=self._sample_pages,
            total_chars=total_chars,
            valid_chars=valid_chars,
            total_lines=total_lines,
            meaningful_lines=meaningful_lines,
            reason=reason,
        )

    @staticmethod
    def _extract_sample_text(file_path: str, sample_pages: int) -> str:
        """Extract plain text from the first *sample_pages* pages via pypdf."""
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ImportError(
                    "DocumentQualityChecker requires 'pypdf' (preferred) or "
                    "'PyPDF2'. Install with: pip install pypdf"
                ) from exc

        reader = PdfReader(file_path)
        texts: list[str] = []
        for page_idx, page in enumerate(reader.pages):
            if page_idx >= sample_pages:
                break
            try:
                page_text = page.extract_text()
            except Exception:
                continue
            if page_text:
                texts.append(page_text)

        return "\n".join(texts)
