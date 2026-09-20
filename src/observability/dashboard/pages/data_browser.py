"""Data browser page — document list, chunk detail, and image preview."""

from __future__ import annotations

import hashlib

import streamlit as st

from observability.dashboard.services.data_service import DataService

COLLECTION_OPTIONS_LIMIT = 50
ALL_COLLECTIONS = "（全部）"


def _safe_key(prefix: str, value: str) -> str:
    """Build a Streamlit-safe widget key by hashing non-alphanumeric values."""
    safe = hashlib.md5(value.encode()).hexdigest()[:12]
    return f"{prefix}_{safe}"


def _render_document_list(svc: DataService, collection_filter: str | None) -> None:
    docs = svc.list_documents(collection=collection_filter)
    if not docs:
        st.info("暂无文档，请先在「摄取管理」页面摄取数据。")
        return

    st.caption(f"共找到 {len(docs)} 个文档")

    for i, doc in enumerate(docs):
        sp = doc["source_path"]
        with st.expander(
            f"{sp} — {doc['chunk_count']} 个分块，"
            f"{doc['image_count']} 张图片",
            expanded=(i == 0 and len(docs) == 1),
        ):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.caption(f"**集合：** {doc['collection']}")
                st.caption(f"**路径：** {sp}")
                if doc.get("ingested_at"):
                    st.caption(f"**摄取时间：** {doc['ingested_at']}")
            with col2:
                st.metric("分块数", doc["chunk_count"])
                st.metric("图片数", doc["image_count"])

            show_chunks = st.checkbox(
                "显示分块内容",
                key=_safe_key("show_chunks", f"{i}_{sp}"),
            )
            if show_chunks:
                chunks = svc.get_chunks_for_document(
                    sp, collection=doc["collection"]
                )
                _render_chunks(chunks)

            if doc["image_count"] > 0:
                show_images = st.checkbox(
                    "显示图片",
                    key=_safe_key("show_images", f"{i}_{sp}"),
                )
                if show_images:
                    images = svc.get_images_for_document(doc["collection"])
                    _render_images(svc, images)


def _render_chunks(chunks) -> None:
    if not chunks:
        st.info("暂无分块。")
        return

    for j, chunk in enumerate(chunks):
        chunk_id = chunk.get("id", str(j))
        chunk_key = _safe_key("chunk", chunk_id)

        with st.container(border=True):
            st.caption(f"**分块 {j + 1}** — ID：`{chunk_id}`")

            text = chunk.get("text", "")
            if len(text) > 500:
                show_full = st.checkbox(
                    "显示全文",
                    key=f"full_{chunk_key}",
                )
                st.write(text if show_full else text[:500] + "...")
            else:
                st.write(text)

            metadata = chunk.get("metadata", {})
            if metadata:
                with st.expander("元数据", expanded=False):
                    st.json(metadata)


def _render_images(svc: DataService, images) -> None:
    import os

    for j, img in enumerate(images):
        file_path = img.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            st.caption(f"图片 {j + 1}：文件不存在 `{file_path}`")
            continue

        b64 = svc.get_image_base64(file_path)
        if b64:
            st.caption(
                f"**图片 {j + 1}** — `{img.get('image_id', 'N/A')}` "
                f"（第 {img.get('page_num', '?')} 页）"
            )
            st.image(b64, use_container_width=True)
        else:
            st.caption(f"图片 {j + 1}：无法加载 `{file_path}`")


def main() -> None:
    st.title("数据浏览")
    st.caption("浏览已入库的文档、分块与图片。")

    svc = DataService()

    with st.sidebar:
        st.subheader("筛选")
        collections = svc.get_collections()
        all_options = [ALL_COLLECTIONS] + collections[:COLLECTION_OPTIONS_LIMIT]
        selected = st.selectbox("集合", all_options)
        collection_filter = None if selected == ALL_COLLECTIONS else selected

    _render_document_list(svc, collection_filter)


main()
