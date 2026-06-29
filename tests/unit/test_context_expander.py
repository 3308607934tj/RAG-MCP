"""Unit tests for ContextExpander — neighbor-chunk context enrichment."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

# Avoid ingestion-level import dependency graph.
_spec = __import__("importlib").util.spec_from_file_location(
    "core.query_engine.context_expander",
    Path(__file__).resolve().parents[2]
    / "src"
    / "core"
    / "query_engine"
    / "context_expander.py",
)
_mod = __import__("importlib").util.module_from_spec(_spec)
sys.modules["core.query_engine.context_expander"] = _mod
_spec.loader.exec_module(_mod)

ContextExpander = _mod.ContextExpander

# Lightweight fake RetrievalResult
from dataclasses import dataclass, field


@dataclass
class _FakeResult:
    chunk_id: str
    score: float
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers


def _make_store(records_by_doc: Dict[str, List[Dict[str, Any]]]) -> MagicMock:
    """Return a mock vector store whose ``get_by_metadata`` returns
    the list of records for the given ``parent_doc_id`` filter."""

    def _get_by_metadata(filters: dict) -> List[Dict[str, Any]]:
        doc_id = filters.get("parent_doc_id", "")
        return records_by_doc.get(doc_id, [])

    store = MagicMock()
    store.get_by_metadata.side_effect = _get_by_metadata
    return store


def _r(
    chunk_id: str,
    text: str,
    parent_doc_id: str = "doc_a",
    chunk_index: int = 0,
    score: float = 0.9,
    **extra_meta,
) -> _FakeResult:
    return _FakeResult(
        chunk_id=chunk_id,
        score=score,
        text=text,
        metadata={"parent_doc_id": parent_doc_id, "chunk_index": chunk_index, **extra_meta},
    )


# ------------------------------------------------------------------ tests


class TestContextExpander:
    """Core expansion logic."""

    def test_single_result_with_neighbors(self):
        """Hit chunk 2 in a 5-chunk document → prev=1, next=3."""
        store = _make_store(
            {
                "doc_a": [
                    {"id": "c0", "text": "Chunk 0", "metadata": {"chunk_index": 0}},
                    {"id": "c1", "text": "Chunk 1", "metadata": {"chunk_index": 1}},
                    {"id": "c2", "text": "Chunk 2", "metadata": {"chunk_index": 2}},
                    {"id": "c3", "text": "Chunk 3", "metadata": {"chunk_index": 3}},
                    {"id": "c4", "text": "Chunk 4", "metadata": {"chunk_index": 4}},
                ]
            }
        )
        expander = ContextExpander(store, window_size=1)
        results = [_r("c2", "Hit text", chunk_index=2)]

        expanded = expander.expand(results)

        assert len(expanded) == 1
        assert expanded[0].metadata.get("context_expanded") is True
        assert expanded[0].metadata.get("context_window") == 1
        text = expanded[0].text
        assert "上文" in text
        assert "Chunk 1" in text
        assert "Hit text" in text
        assert "下文" in text
        assert "Chunk 3" in text
        assert "Chunk 0" not in text  # outside window
        assert "Chunk 4" not in text  # outside window

    def test_window_size_2(self):
        """window_size=2 → 2 before + 2 after."""
        store = _make_store(
            {
                "doc_a": [
                    {"id": f"c{i}", "text": f"Chunk {i}", "metadata": {"chunk_index": i}}
                    for i in range(7)
                ]
            }
        )
        expander = ContextExpander(store, window_size=2)
        results = [_r("c3", "Hit", chunk_index=3)]

        expanded = expander.expand(results)
        text = expanded[0].text
        assert "Chunk 1" in text  # prev-2
        assert "Chunk 2" in text  # prev-1
        assert "Chunk 4" in text  # next+1
        assert "Chunk 5" in text  # next+2
        assert "Chunk 0" not in text  # outside window

    def test_boundary_first_chunk(self):
        """chunk_index=0 → no prev, only next."""
        store = _make_store(
            {
                "doc_a": [
                    {"id": "c0", "text": "First", "metadata": {"chunk_index": 0}},
                    {"id": "c1", "text": "Second", "metadata": {"chunk_index": 1}},
                ]
            }
        )
        expander = ContextExpander(store, window_size=1)
        results = [_r("c0", "First", chunk_index=0)]

        expanded = expander.expand(results)
        text = expanded[0].text
        assert "上文" not in text
        assert "First" in text
        assert "下文" in text
        assert "Second" in text

    def test_boundary_last_chunk(self):
        """Last chunk → no next, only prev."""
        store = _make_store(
            {
                "doc_a": [
                    {"id": "c0", "text": "First", "metadata": {"chunk_index": 0}},
                    {"id": "c1", "text": "Last", "metadata": {"chunk_index": 1}},
                ]
            }
        )
        expander = ContextExpander(store, window_size=1)
        results = [_r("c1", "Last", chunk_index=1)]

        expanded = expander.expand(results)
        text = expanded[0].text
        assert "上文" in text
        assert "First" in text
        assert "Last" in text
        assert "下文" not in text

    def test_no_parent_doc_id_passes_through(self):
        """Result without parent_doc_id is returned unchanged."""
        store = _make_store({})
        expander = ContextExpander(store, window_size=1)
        result = _r("x", "No doc", parent_doc_id="", chunk_index=0)
        result.metadata.pop("parent_doc_id", None)

        expanded = expander.expand([result])
        assert expanded[0].text == "No doc"
        assert expanded[0].metadata.get("context_expanded") is None

    def test_no_chunk_index_passes_through(self):
        """Result without chunk_index is returned unchanged."""
        store = _make_store({})
        expander = ContextExpander(store, window_size=1)
        result = _r("x", "No idx", chunk_index=None)  # type: ignore
        result.metadata.pop("chunk_index", None)

        expanded = expander.expand([result])
        assert expanded[0].text == "No idx"

    def test_window_zero_returns_unchanged(self):
        """window_size=0 → no expansion at all."""
        store = _make_store(
            {"doc_a": [{"id": "c0", "text": "T", "metadata": {"chunk_index": 0}}]}
        )
        expander = ContextExpander(store, window_size=0)
        results = [_r("c0", "Original", chunk_index=0)]

        expanded = expander.expand(results)
        assert expanded[0].text == "Original"

    def test_empty_results(self):
        """Empty list → empty list."""
        store = _make_store({})
        expander = ContextExpander(store, window_size=1)
        assert expander.expand([]) == []

    def test_multiple_results_same_doc_shares_query(self):
        """Two hits from same document → single get_by_metadata call."""
        records = [
            {"id": f"c{i}", "text": f"Chunk {i}", "metadata": {"chunk_index": i}}
            for i in range(3)
        ]
        store = _make_store({"doc_a": records})

        expander = ContextExpander(store, window_size=1)
        results = [
            _r("c0", "C0", chunk_index=0),
            _r("c2", "C2", chunk_index=2),
        ]

        expanded = expander.expand(results)
        assert len(expanded) == 2
        assert expanded[0].metadata.get("context_expanded") is True
        assert expanded[1].metadata.get("context_expanded") is True
        # Only ONE call for "doc_a" (not N per result)
        assert store.get_by_metadata.call_count == 1

    def test_store_error_degrades_gracefully(self):
        """If get_by_metadata raises, results pass through unchanged."""
        store = MagicMock()
        store.get_by_metadata.side_effect = RuntimeError("Chroma is down")
        expander = ContextExpander(store, window_size=1)
        results = [_r("c0", "Survive", chunk_index=0)]

        expanded = expander.expand(results)
        assert expanded[0].text == "Survive"

    def test_negative_window_size_raises(self):
        """window_size < 0 → ValueError."""
        store = _make_store({})
        with pytest.raises(ValueError, match="window_size"):
            ContextExpander(store, window_size=-1)
