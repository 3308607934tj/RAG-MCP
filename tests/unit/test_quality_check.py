"""Unit tests for the pre-ingestion document quality gate (Layer-1).

Because the ingestion __init__ eagerly imports embedding (which requires
sentence-transformers), we load the quality module directly to keep these
tests self-contained.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load quality module without triggering ingestion.__init__
_quality_spec = importlib.util.spec_from_file_location(
    "ingestion.quality",
    Path(__file__).resolve().parents[2] / "src" / "ingestion" / "quality" / "__init__.py",
)
_quality = importlib.util.module_from_spec(_quality_spec)
sys.modules["ingestion.quality"] = _quality
_quality_spec.loader.exec_module(_quality)

# Local aliases
DocumentQualityChecker = _quality.DocumentQualityChecker
DocumentRejectedError = _quality.DocumentRejectedError
QualityCheckResult = _quality.QualityCheckResult
_is_valid_char = _quality._is_valid_char
_is_word_char = _quality._is_word_char
QualityCheckSettings = _quality.QualityCheckSettings


# ── Character helpers ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "ch, expected",
    [
        ("A", True),
        ("z", True),
        ("0", True),
        (" ", True),
        ("\t", True),
        ("\n", True),
        ("中", True),   # CJK 中
        ("あ", True),   # Hiragana あ
        ("\x00", False),    # NULL
        ("\x01", False),    # SOH
        ("", False),  # Private use area
        ("￾", False),  # Noncharacter
        ("�", False),  # Replacement character
    ],
)
def test_is_valid_char(ch: str, expected: bool):
    assert _is_valid_char(ch) is expected


@pytest.mark.parametrize(
    "ch, expected",
    [
        ("A", True),
        ("中", True),   # CJK 中
        ("가", True),   # Hangul 가
        (".", False),
        (" ", False),
    ],
)
def test_is_word_char(ch: str, expected: bool):
    assert _is_word_char(ch) is expected


# ── QualityChecker unit tests ───────────────────────────────────────


def _make_checker(**overrides):
    """Create a checker with sensible test defaults and overrides."""
    kwargs = {
        "enabled": True,
        "sample_pages": 3,
        "min_valid_char_ratio": 0.25,
        "min_text_density": 0.1,
    }
    kwargs.update(overrides)
    return DocumentQualityChecker(QualityCheckSettings(**kwargs))


class TestQualityChecker:
    """Core quality-check logic, all with mocked pypdf extraction."""

    def test_good_text_passes(self):
        checker = _make_checker()
        text = "## Introduction\n\nThis is a well-formed document.\n\nThe quick brown fox jumps over the lazy dog."
        with patch.object(checker, "_extract_sample_text", return_value=text):
            result = checker.check("dummy.pdf")
            assert result.passed
            assert result.valid_char_ratio > 0.9
            assert result.text_density > 0.5

    def test_chinese_text_passes(self):
        checker = _make_checker()
        text = (
            "第一章 系统架构设计\n\n"
            "本系统采用模块化RAG架构，支持多模态检索。\n\n"
            "核心组件包括文档加载器、切分器、向量编码器等。"
        )
        with patch.object(checker, "_extract_sample_text", return_value=text):
            result = checker.check("dummy.pdf")
            assert result.passed
            assert result.valid_char_ratio > 0.95
            assert result.text_density > 0.5

    def test_garbled_text_rejected(self):
        """High ratio of non-printable chars → reject."""
        checker = _make_checker()
        text = "\x00\x01\x02\x03\x04\x05\x06\x07\x08" * 100 + "hello"
        with patch.object(checker, "_extract_sample_text", return_value=text):
            with pytest.raises(DocumentRejectedError) as exc_info:
                checker.check("garbage.pdf")
            assert "valid_char_ratio" in str(exc_info.value)
            assert exc_info.value.result.valid_char_ratio < 0.25
            assert not exc_info.value.result.passed

    def test_replacement_char_heavy_text_rejected(self):
        """Heavy U+FFFD chars → low ratio → reject."""
        checker = _make_checker()
        text = "�" * 200 + "A" * 10
        with patch.object(checker, "_extract_sample_text", return_value=text):
            with pytest.raises(DocumentRejectedError):
                checker.check("broken.pdf")

    def test_empty_text_rejected(self):
        """Zero text → ratio 0 → reject."""
        checker = _make_checker()
        with patch.object(checker, "_extract_sample_text", return_value=""):
            with pytest.raises(DocumentRejectedError) as exc_info:
                checker.check("empty.pdf")
            assert exc_info.value.result.valid_char_ratio == 0.0
            assert exc_info.value.result.text_density == 0.0

    def test_disabled_check_always_passes(self):
        """When enabled=False, bypass the gate entirely."""
        checker = _make_checker(enabled=False)
        with patch.object(checker, "_extract_sample_text", return_value=""):
            result = checker.check("any.pdf")
            assert result.passed
            assert result.reason == "quality_check disabled"

    def test_low_text_density_fails_even_with_clean_chars(self):
        """Valid chars but each line is short → low density → reject."""
        checker = _make_checker(min_valid_char_ratio=0.1, min_text_density=0.3)
        # "hi", "ok", "no", "go" each have only 2 word-chars (< 3 threshold)
        lines = ["hi", "ok", "no", "go"] * 10
        text = "\n".join(lines)
        with patch.object(checker, "_extract_sample_text", return_value=text):
            with pytest.raises(DocumentRejectedError) as exc_info:
                checker.check("sparse.pdf")
            assert exc_info.value.result.text_density == 0.0

    def test_mixed_cjk_english_passes(self):
        checker = _make_checker()
        text = (
            "## Requirements 需求\n\n"
            "系统需要 REST API for 外部集成。\n\n"
            "Backend: Python FastAPI with async。\n\n"
            "Schema:\n- users: id, name, email\n- docs: id, content"
        )
        with patch.object(checker, "_extract_sample_text", return_value=text):
            result = checker.check("mixed.pdf")
            assert result.passed
            assert result.valid_char_ratio > 0.95

    def test_short_clean_text_passes(self):
        checker = _make_checker()
        text = "Hello world. This is a test."
        with patch.object(checker, "_extract_sample_text", return_value=text):
            result = checker.check("tiny.pdf")
            assert result.passed


class TestDocumentRejectedError:
    """DocumentRejectedError carries a full QualityCheckResult."""

    def test_error_carries_result(self):
        result = QualityCheckResult(
            passed=False,
            valid_char_ratio=0.1,
            text_density=0.05,
            sampled_pages=3,
            total_chars=1000,
            valid_chars=100,
            total_lines=50,
            meaningful_lines=2,
            reason="test rejection",
        )
        err = DocumentRejectedError("bad.pdf", result)
        assert "bad.pdf" in str(err)
        assert "0.100" in str(err)
        assert err.result is result


class TestQualityCheckResult:
    """QualityCheckResult dataclass."""

    def test_passed_result(self):
        r = QualityCheckResult(
            passed=True,
            valid_char_ratio=0.95,
            text_density=0.8,
            sampled_pages=3,
            total_chars=500,
            valid_chars=475,
            total_lines=20,
            meaningful_lines=16,
        )
        assert r.passed

    def test_failed_result_with_reason(self):
        r = QualityCheckResult(
            passed=False,
            valid_char_ratio=0.05,
            text_density=0.01,
            sampled_pages=3,
            total_chars=100,
            valid_chars=5,
            total_lines=10,
            meaningful_lines=0,
            reason="valid_char_ratio=0.050 < threshold=0.25",
        )
        assert not r.passed
        assert "threshold" in r.reason
