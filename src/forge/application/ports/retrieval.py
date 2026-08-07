"""Retrieval ports. Declared in v1, implemented in v2.

Split into three so the Hybrid RAG pipeline's stages stay independently
swappable and independently measurable:

    query -> KeywordSearchPort (BM25)  ─┐
          -> VectorStorePort  (dense)  ─┼─> RRF ─> RerankerPort ─> top-k
          -> metadata prefilter        ─┘

The stages are separate ports specifically so the ablation study is possible:
BM25-only, dense-only, fused, and reranked can each be run and scored against
the same golden question set. That ablation table is the highest-value artefact
for justifying the retrieval design, and it is only cheap to produce if the
seams exist from the start.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    chunk_id: str
    title: str
    text: str
    # Filterable dimensions: pack, station, defect family, date. Metadata
    # prefiltering is what makes spec-table lookups work at all, since neither
    # BM25 nor embeddings handle "the torque table for this variant" well.
    metadata: dict[str, str] = field(default_factory=dict)
    source_kind: str = "sop"


@dataclass(frozen=True, slots=True)
class ScoredDocument:
    document: Document
    score: float
    # Per-stage scores, retained so the Retrieval Playground can show exactly
    # where a document was promoted or demoted.
    bm25_score: float | None = None
    dense_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


class KeywordSearchPort(ABC):
    """Lexical retrieval. Wins on part numbers, torque specs, jargon."""

    @abstractmethod
    async def search(
        self, query: str, *, top_k: int = 30, metadata_filter: dict[str, str] | None = None
    ) -> tuple[ScoredDocument, ...]: ...

    @abstractmethod
    async def index(self, documents: Sequence[Document]) -> int: ...


class VectorStorePort(ABC):
    """Dense retrieval. Wins on narrative incident reports and paraphrase."""

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        collection: str,
        top_k: int = 30,
        metadata_filter: dict[str, str] | None = None,
    ) -> tuple[ScoredDocument, ...]: ...

    @abstractmethod
    async def upsert(self, collection: str, documents: Sequence[Document]) -> int: ...

    @abstractmethod
    async def collection_stats(self, collection: str) -> dict[str, int]: ...


class RerankerPort(ABC):
    """Cross-encoder rerank of the fused candidate set."""

    @abstractmethod
    async def rerank(
        self, query: str, candidates: Sequence[ScoredDocument], *, top_k: int = 5
    ) -> tuple[ScoredDocument, ...]: ...
