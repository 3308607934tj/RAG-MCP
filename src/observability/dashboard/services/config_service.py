"""Configuration reading service for the Dashboard UI."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from core.settings import Settings, load_settings


class ConfigService:
    """Encapsulates Settings reading and formats component config for display."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    def get_component_cards(self) -> List[Dict[str, Any]]:
        """Return a list of component configuration cards for the overview page."""
        s = self.settings
        return [
            {
                "name": "大语言模型",
                "icon": "🤖",
                "fields": {
                    "服务商": s.llm.provider,
                    "模型": s.llm.model,
                    "温度": str(s.llm.temperature),
                    "最大 Token 数": str(s.llm.max_tokens),
                },
                "status": "active" if s.llm.provider else "inactive",
            },
            {
                "name": "向量模型",
                "icon": "🧮",
                "fields": {
                    "服务商": s.embedding.provider,
                    "模型": s.embedding.model,
                    "向量维度": str(s.embedding.dimensions),
                },
                "status": "active" if s.embedding.provider else "inactive",
            },
            {
                "name": "向量库",
                "icon": "🗄️",
                "fields": {
                    "服务商": s.vector_store.provider,
                    "持久化目录": s.vector_store.persist_directory,
                    "集合名称": s.vector_store.collection_name,
                },
                "status": "active" if s.vector_store.provider else "inactive",
            },
            {
                "name": "检索参数",
                "icon": "🔎",
                "fields": {
                    "Dense 召回数": str(s.retrieval.dense_top_k),
                    "Sparse 召回数": str(s.retrieval.sparse_top_k),
                    "融合召回数": str(s.retrieval.fusion_top_k),
                    "RRF 常数 k": str(s.retrieval.rrf_k),
                },
                "status": "active",
            },
            {
                "name": "重排序",
                "icon": "📊",
                "fields": {
                    "是否启用": str(s.rerank.enabled),
                    "服务商": s.rerank.provider,
                    "模型": s.rerank.model or "（默认）",
                    "保留条数": str(s.rerank.top_k),
                },
                "status": "active" if s.rerank.enabled else "inactive",
            },
            {
                "name": "评估",
                "icon": "📈",
                "fields": {
                    "是否启用": str(s.evaluation.enabled),
                    "服务商": s.evaluation.provider,
                    "评估指标": ", ".join(s.evaluation.metrics),
                },
                "status": "active" if s.evaluation.enabled else "inactive",
            },
            {
                "name": "可观测性",
                "icon": "👁️",
                "fields": {
                    "日志级别": s.observability.log_level,
                    "是否开启追踪": str(s.observability.trace_enabled),
                    "追踪文件": s.observability.trace_file,
                },
                "status": "active",
            },
            {
                "name": "视觉模型",
                "icon": "👁️‍🗨️",
                "fields": {
                    "是否启用": str(s.vision_llm.enabled),
                    "服务商": s.vision_llm.provider,
                    "模型": s.vision_llm.model,
                },
                "status": "active" if s.vision_llm.enabled else "inactive",
            },
        ]

    def get_ingestion_config(self) -> Dict[str, Any] | None:
        s = self.settings
        if s.ingestion is None:
            return None
        return {
            "分块大小": str(s.ingestion.chunk_size),
            "分块重叠": str(s.ingestion.chunk_overlap),
            "切分器": s.ingestion.splitter,
            "批处理大小": str(s.ingestion.batch_size),
            "分块精炼（LLM）": (
                str(s.ingestion.chunk_refiner.use_llm)
                if s.ingestion.chunk_refiner
                else "N/A"
            ),
            "元数据增强（LLM）": (
                str(s.ingestion.metadata_enricher.use_llm)
                if s.ingestion.metadata_enricher
                else "N/A"
            ),
            "图片描述（视觉模型）": (
                str(s.ingestion.image_captioner.use_vision_llm)
                if s.ingestion.image_captioner
                else "N/A"
            ),
        }
