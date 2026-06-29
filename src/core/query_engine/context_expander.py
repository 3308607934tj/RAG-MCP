"""ContextExpander — expand retrieval results with neighbor chunks.

After reranking, ContextExpander enriches each ``RetrievalResult`` by
fetching its preceding and succeeding chunks (by ``parent_doc_id`` +
``chunk_index``) from the vector store and concatenating them around
the original text.  This gives the LLM the surrounding document context
without diluting the reranker's precision signal.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.types import RetrievalResult

logger = logging.getLogger(__name__)

# Boundary markers for expanded context segments.
_PREFIX_MARKER = "\n\n--- 上文（来自同一文档）---\n"
_SUFFIX_MARKER = "\n--- 下文（来自同一文档）---\n\n"


class ContextExpander:
    """Fetch and attach neighbor chunks around each retrieval result.

    Parameters
    ----------
    vector_store:
        A vector-store instance that exposes ``get_by_metadata(filters)``
        returning ``List[{"id": str, "text": str, "metadata": dict}]``.
    window_size:
        Number of chunks to include **before** and **after** the hit.
        Default 1 → 3 chunks total (prev + hit + next).
    """

    def __init__(
        self,
        vector_store: Any,
        *,
        window_size: int = 1,
    ):
        if window_size < 0:
            raise ValueError("window_size must be >= 0")
        self._vector_store = vector_store
        self._window_size = window_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def expand(
        self, results: List[RetrievalResult]
    ) -> List[RetrievalResult]:
        """Enrich each result's ``.text`` with surrounding chunks.

        Results without a usable ``parent_doc_id`` / ``chunk_index`` are
        returned unchanged.  Multiple results from the same document share
        a single vector-store query.
        """
        if not results or self._window_size == 0:
            return results

        # 1. Group results by parent_doc_id, keeping original order.
        groups: Dict[str, List[tuple[int, RetrievalResult]]] = {}
        for idx, result in enumerate(results):
            doc_id = self._resolve_doc_id(result)
            if doc_id is None:
                continue
            groups.setdefault(doc_id, []).append((idx, result))

        if not groups:
            return results

        # 2. Fetch all chunks per unique document (one query each).
        doc_neighbors: Dict[str, Dict[int, str]] = {}
        for doc_id in groups:
            try:
                records = self._vector_store.get_by_metadata(
                    {"parent_doc_id": doc_id}
                )
                doc_neighbors[doc_id] = self._build_index_map(records)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                logger.warning(
                    "ContextExpander: failed to fetch neighbors for %s: %s",
                    doc_id,
                    exc,
                )
                doc_neighbors[doc_id] = {}

        # 3. Expand each result.
        for doc_id, indexed_results in groups.items():
            neighbor_map = doc_neighbors.get(doc_id, {})
            if not neighbor_map:
                continue

            for idx, result in indexed_results:
                chunk_index = self._resolve_chunk_index(result)
                if chunk_index is None:
                    continue

                expanded_text = self._build_expanded_text(
                    hit_text=result.text,
                    chunk_index=chunk_index,
                    neighbor_map=neighbor_map,
                )
                result.text = expanded_text
                result.metadata["context_expanded"] = True
                result.metadata["context_window"] = self._window_size

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_doc_id(result: RetrievalResult) -> Optional[str]:
        """Extract parent_doc_id from metadata; return None if missing."""
        md = result.metadata or {}
        doc_id = md.get("parent_doc_id")
        return doc_id if isinstance(doc_id, str) and doc_id.strip() else None

    @staticmethod
    def _resolve_chunk_index(result: RetrievalResult) -> Optional[int]:
        """Extract chunk_index from metadata; return None if missing."""
        md = result.metadata or {}
        idx = md.get("chunk_index")
        try:
            return int(idx)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _build_expanded_text(
        self,
        hit_text: str,
        chunk_index: int,
        neighbor_map: Dict[int, str],
    ) -> str:
        """Assemble `[prev...] + hit + [next...]` around *chunk_index*."""
        prev_parts: List[str] = []
        for offset in range(1, self._window_size + 1):
            neighbor_text = neighbor_map.get(chunk_index - offset)
            if neighbor_text is not None:
                prev_parts.append(neighbor_text)

        next_parts: List[str] = []
        for offset in range(1, self._window_size + 1):
            neighbor_text = neighbor_map.get(chunk_index + offset)
            if neighbor_text is not None:
                next_parts.append(neighbor_text)

        segments: List[str] = []

        if prev_parts:
            # Closest prev chunk last so it reads naturally.
            segments.append(_PREFIX_MARKER + "\n\n".join(reversed(prev_parts)))

        segments.append(hit_text)

        if next_parts:
            segments.append(_SUFFIX_MARKER + "\n\n".join(next_parts))

        return "\n".join(segments) if len(segments) > 1 else hit_text

    @staticmethod
    def _build_index_map(records: List[Dict[str, Any]]) -> Dict[int, str]:
        """Build ``{chunk_index: text}`` from vector-store records.

        Records must have ``metadata.chunk_index`` as an integer.
        Missing or invalid indices are silently skipped.
        """
        index_map: Dict[int, str] = {}
        for record in records:
            meta = record.get("metadata") or {}
            try:
                idx = int(meta["chunk_index"])
            except (KeyError, TypeError, ValueError):
                continue
            text = record.get("text")
            if isinstance(text, str) and text.strip():
                index_map[idx] = text
        return index_map
