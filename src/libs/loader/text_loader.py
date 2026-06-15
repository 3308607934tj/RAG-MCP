"""Plain text loader for non-PDF files (.go, .sql, .md, .yaml, .json, .txt, etc.)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from core.types import Document, validate_document_contract

from .base_loader import BaseLoader

logger = logging.getLogger(__name__)

# Supported text file extensions
TEXT_EXTENSIONS = {
    ".go", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp",
    ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash",
    ".sql", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini",
    ".cfg", ".conf", ".xml", ".html", ".css", ".scss", ".less",
    ".vue", ".svelte", ".jsx", ".tsx", ".env", ".dockerfile",
    ".gitignore", ".dockerignore", ".editorconfig", ".prettierrc",
}


class TextLoader(BaseLoader):
    """Load plain text files into a Document."""

    def __init__(self, encoding: str = "utf-8", errors: str = "ignore"):
        self.encoding = encoding
        self.errors = errors

    def load(self, path: str, trace: Optional[Any] = None) -> Document:
        fp = Path(path)
        if not fp.is_file():
            raise FileNotFoundError(f"File not found: {fp}")

        doc_hash = self._compute_file_hash(fp)
        text = fp.read_text(encoding=self.encoding, errors=self.errors)
        text = text.strip()

        line_count = text.count("\n") + 1 if text else 0

        metadata: Dict[str, Any] = {
            "source_path": str(fp.resolve()),
            "file_extension": fp.suffix.lower(),
            "line_count": line_count,
        }

        document = Document(
            id=doc_hash,
            text=text,
            metadata=metadata,
        )
        validate_document_contract(document)
        return document

    @staticmethod
    def _compute_file_hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
