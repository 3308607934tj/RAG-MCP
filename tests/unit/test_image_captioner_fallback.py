"""Unit tests for ImageCaptioner fallback behavior (C7).

Because ingestion.__init__ eagerly imports embedding (which requires
sentence-transformers), we bypass the ingestion package and load
image_captioner directly with a minimal namespace.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import yaml

_SRC = Path(__file__).resolve().parents[2] / "src"

# ── Build a minimal ingestion.transform namespace ────────────────────
# These injections are needed ONLY while image_captioner is exec'd below.
# Snapshot the real entries first so they can be restored afterwards:
# leaving the fakes in sys.modules breaks other test modules that resolve
# these names later (see docs/REPRODUCTION_NOTES.md, issue 11).
_INJECTED_KEYS = (
    "ingestion",
    "ingestion.transform",
    "ingestion.transform.base_transform",
    "ingestion.transform.image_captioner",
)
_SAVED_MODULES = {key: sys.modules.get(key) for key in _INJECTED_KEYS}

# Create ingestion package (no eager imports)
_ingestion = ModuleType("ingestion")
_ingestion.__path__ = [str(_SRC / "ingestion")]
sys.modules["ingestion"] = _ingestion

# Load base_transform via spec (its only imports are from core)
_bt_spec = importlib.util.spec_from_file_location(
    "ingestion.transform.base_transform",
    _SRC / "ingestion" / "transform" / "base_transform.py",
)
_bt_mod = importlib.util.module_from_spec(_bt_spec)
sys.modules["ingestion.transform.base_transform"] = _bt_mod
_bt_spec.loader.exec_module(_bt_mod)

# Create ingestion.transform package
_transform = ModuleType("ingestion.transform")
_transform.__path__ = [str(_SRC / "ingestion" / "transform")]
_transform.BaseTransform = _bt_mod.BaseTransform  # re-export
sys.modules["ingestion.transform"] = _transform

# Now load image_captioner safely
_ic_spec = importlib.util.spec_from_file_location(
    "ingestion.transform.image_captioner",
    _SRC / "ingestion" / "transform" / "image_captioner.py",
)
_ic_mod = importlib.util.module_from_spec(_ic_spec)
sys.modules["ingestion.transform.image_captioner"] = _ic_mod
_ic_spec.loader.exec_module(_ic_mod)

# Restore the real modules: the fakes were only needed for the exec above.
for _key, _original in _SAVED_MODULES.items():
    if _original is None:
        sys.modules.pop(_key, None)
    else:
        sys.modules[_key] = _original

ImageCaptioner = _ic_mod.ImageCaptioner
_inject_captions_into_text = _ic_mod._inject_captions_into_text

from core.settings import Settings
from core.trace.trace_context import TraceContext
from core.types import Chunk
from libs.llm.base_vision_llm import BaseVisionLLM, VisionLLMSettings

_MINIMAL_SETTINGS_YAML = """
llm:
  provider: openai
  model: gpt-4o
  temperature: 0.0
  max_tokens: 1024
  api_key: test-key
embedding:
  provider: openai
  model: text-embedding-3-small
  dimensions: 1536
  api_key: ""
vector_store:
  provider: chroma
  persist_directory: ./data/db/chroma
retrieval:
  dense_top_k: 20
  sparse_top_k: 20
  fusion_top_k: 10
  rrf_k: 60
rerank:
  enabled: false
  provider: none
evaluation:
  enabled: false
  provider: custom
  metrics: [hit_rate]
observability:
  log_level: INFO
  trace_enabled: false
  trace_file: ./logs/traces.jsonl
  structured_logging: false
vision_llm:
  enabled: {vision_enabled}
  provider: openai
  model: gpt-4o
ingestion:
  chunk_size: 100
  chunk_overlap: 0
  splitter: recursive
  batch_size: 10
  chunk_refiner:
    use_llm: false
  metadata_enricher:
    use_llm: false
  image_captioner:
    use_vision_llm: {use_vision_llm}
"""


def _settings(*, use_vision_llm: bool, vision_enabled: bool = True) -> Settings:
    raw = yaml.safe_load(
        _MINIMAL_SETTINGS_YAML.format(
            use_vision_llm=str(use_vision_llm).lower(),
            vision_enabled=str(vision_enabled).lower(),
        )
    )
    return Settings.from_dict(raw)


def _chunk_with_image(cid: str = "c1") -> Chunk:
    return Chunk(
        id=cid,
        text="See figure [IMAGE: img_1] for architecture.",
        metadata={
            "source_path": "/docs/spec.pdf",
            "image_refs": ["img_1"],
            "images": [{"id": "img_1", "path": "/tmp/img_1.png"}],
        },
        start_offset=0,
        end_offset=40,
        source_ref="doc1",
    )


class _FakeVisionLLM(BaseVisionLLM):
    def __init__(self, caption: str = "architecture diagram", fail: bool = False):
        super().__init__(VisionLLMSettings(provider="openai", model="gpt-4o", api_key="k"))
        self._caption = caption
        self._fail = fail
        self.calls = 0

    def chat_with_image(self, text: str, image_path=None, **kwargs) -> str:  # type: ignore[override]
        return self.describe_image(str(image_path), prompt=text)

    def describe_image(self, image_path: str, prompt: str = None) -> str:  # type: ignore[override]
        self.calls += 1
        if self._fail:
            raise RuntimeError("vision api down")
        return self._caption


def test_captioner_uses_vision_llm_when_enabled() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    fake = _FakeVisionLLM(caption="A system architecture figure.")
    cap = ImageCaptioner(s, vision_llm=fake)

    out = cap.transform([_chunk_with_image()])[0]

    assert fake.calls == 1
    assert out.metadata["image_captions"]["img_1"] == "A system architecture figure."
    assert "has_unprocessed_images" not in out.metadata
    # Caption injected into chunk.text for retrieval embedding
    assert "[图片描述: A system architecture figure.]" in out.text
    assert "[IMAGE: img_1]" in out.text  # placeholder preserved


def test_captioner_inject_into_text_does_not_lose_original_content() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    fake = _FakeVisionLLM(caption="3-layer RAG pipeline flow.")
    cap = ImageCaptioner(s, vision_llm=fake)

    chunk = _chunk_with_image()
    out = cap.transform([chunk])[0]

    # Original text still present, caption sits after placeholder
    assert "See figure" in out.text
    assert "for architecture." in out.text
    assert "[图片描述: 3-layer RAG pipeline flow.]" in out.text


def test_captioner_disabled_no_text_modification() -> None:
    """When vision LLM is off, chunk.text must stay unchanged."""
    s = _settings(use_vision_llm=False, vision_enabled=True)
    cap = ImageCaptioner(s, vision_llm=_FakeVisionLLM())

    chunk = _chunk_with_image()
    out = cap.transform([chunk])[0]

    assert out.text == chunk.text  # unchanged
    assert "[图片描述:" not in out.text
    assert "image_captions" not in out.metadata
    assert out.metadata["has_unprocessed_images"] is True
    assert out.metadata["image_refs"] == ["img_1"]


def test_captioner_runtime_failure_falls_back() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    cap = ImageCaptioner(s, vision_llm=_FakeVisionLLM(fail=True))

    out = cap.transform([_chunk_with_image()])[0]

    assert out.metadata["has_unprocessed_images"] is True
    assert out.metadata.get("unprocessed_image_refs") == ["img_1"]
    assert "image_captions" not in out.metadata


def test_captioner_no_image_refs_noop() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    cap = ImageCaptioner(s, vision_llm=_FakeVisionLLM())
    chunk = Chunk(
        id="c2",
        text="No images here.",
        metadata={"source_path": "/docs/spec.pdf"},
        start_offset=0,
        end_offset=14,
        source_ref="doc1",
    )
    out = cap.transform([chunk])[0]
    assert out.metadata == chunk.metadata


def test_captioner_records_trace_stages() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    cap = ImageCaptioner(s, vision_llm=_FakeVisionLLM())
    tr = TraceContext()

    cap.transform([_chunk_with_image()], trace=tr)
    stages = [s.get("stage") for s in tr.stages]

    assert "image_captioner" in stages
    assert "image_captioner_llm_ok" in stages


def test_captioner_requires_ingestion_settings() -> None:
    s = _settings(use_vision_llm=True, vision_enabled=True)
    broken = replace(s, ingestion=None)
    try:
        ImageCaptioner(broken)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "ingestion" in str(exc)


# ── _inject_captions_into_text helper tests ─────────────────────────


def test_inject_replaces_placeholder_with_caption() -> None:
    text = "See [IMAGE: img_1] for details."
    captions = {"img_1": "RAG architecture overview."}
    result = _inject_captions_into_text(text, captions)
    assert "[IMAGE: img_1]" in result
    assert "[图片描述: RAG architecture overview.]" in result
    assert result.startswith("See [IMAGE: img_1]")


def test_inject_handles_multiple_captions() -> None:
    text = "[IMAGE: a] and [IMAGE: b] are shown."
    captions = {"a": "First diagram.", "b": "Second chart."}
    result = _inject_captions_into_text(text, captions)
    assert "[图片描述: First diagram.]" in result
    assert "[图片描述: Second chart.]" in result


def test_inject_missing_placeholder_fallback_appends() -> None:
    """When placeholder not found in text, captions go at the end."""
    text = "No placeholders here."
    captions = {"unknown_id": "Some description."}
    result = _inject_captions_into_text(text, captions)
    assert "No placeholders here." in result
    assert "[图片描述: Some description.]" in result
    # Should be at end
    assert result.endswith("[图片描述: Some description.]")


def test_inject_empty_captions_returns_original() -> None:
    text = "[IMAGE: img_1] alone."
    assert _inject_captions_into_text(text, {}) == text
