"""System overview page — component configuration and data statistics."""

from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from core.settings import REPO_ROOT
from observability.dashboard.services.config_service import ConfigService


def _load_stats() -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "chroma_collections": 0,
        "chroma_entries": 0,
        "bm25_documents": 0,
        "images_stored": 0,
        "ingestion_records": 0,
    }
    try:
        import chromadb

        from core.settings import load_settings

        s = load_settings()
        client = chromadb.PersistentClient(path=s.vector_store.persist_directory)
        try:
            collection = client.get_collection(s.vector_store.collection_name)
            stats["chroma_collections"] = 1
            stats["chroma_entries"] = collection.count()
        except Exception:
            stats["chroma_collections"] = len(client.list_collections())
    except Exception:
        pass

    try:
        from ingestion.storage.bm25_indexer import BM25Indexer

        bm25 = BM25Indexer()
        if bm25.load():
            stats["bm25_documents"] = bm25.doc_count
    except Exception:
        pass

    try:
        import sqlite3

        img_db = REPO_ROOT / "data" / "db" / "image_index.db"
        if img_db.exists():
            conn = sqlite3.connect(str(img_db))
            row = conn.execute("SELECT COUNT(*) FROM image_index").fetchone()
            if row:
                stats["images_stored"] = row[0]
            conn.close()
    except Exception:
        pass

    try:
        import sqlite3

        hist_db = REPO_ROOT / "data" / "db" / "ingestion_history.db"
        if hist_db.exists():
            conn = sqlite3.connect(str(hist_db))
            row = conn.execute(
                "SELECT COUNT(*) FROM ingestion_history WHERE status='success'"
            ).fetchone()
            if row:
                stats["ingestion_records"] = row[0]
            conn.close()
    except Exception:
        pass

    return stats


def main() -> None:
    st.title("系统总览")
    st.caption("Modular RAG 系统的组件配置与数据统计。")

    cfg = ConfigService()

    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("组件配置")
        cards = cfg.get_component_cards()

        # Display in rows of 2
        for i in range(0, len(cards), 2):
            row = st.columns(2)
            for j in range(2):
                idx = i + j
                if idx >= len(cards):
                    break
                card = cards[idx]
                with row[j]:
                    status_color = "green" if card["status"] == "active" else "gray"
                    st.markdown(
                        f"#### {card['icon']} {card['name']} "
                        f"<span style='color:{status_color};font-size:0.8em;'>●</span>",
                        unsafe_allow_html=True,
                    )
                    for label, value in card["fields"].items():
                        st.caption(f"**{label}：** {value}")

        # Ingestion config
        ingestion = cfg.get_ingestion_config()
        if ingestion:
            st.subheader("摄取配置")
            ingest_cols = st.columns(3)
            labels = list(ingestion.items())
            for i, (label, value) in enumerate(labels):
                with ingest_cols[i % 3]:
                    st.caption(f"**{label}：** {value}")

    with col_right:
        st.subheader("数据统计")
        stats = _load_stats()

        st.metric("向量条目数", stats["chroma_entries"])
        st.metric("BM25 文档数", stats["bm25_documents"])
        st.metric("已存图片数", stats["images_stored"])
        st.metric("摄取记录数", stats["ingestion_records"])

        st.divider()

        st.subheader("路径")
        try:
            s = cfg.settings
            st.caption(f"**向量库：** `{s.vector_store.persist_directory}`")
            st.caption(f"**追踪文件：** `{s.observability.trace_file}`")
        except Exception:
            st.caption("配置不可用")


main()
