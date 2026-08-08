"""FORGE knowledge retrieval: ChromaDB ingestion + hybrid (dense + BM25) search.

Ingests the SOP / standard-summary JSON corpus in this folder into a persistent
Chroma collection, chunked at the section level, and serves it back through a
hybrid retriever that fuses dense vector similarity with BM25 lexical matching.
Two fusion strategies are available -- normalised-score (default) and
Reciprocal Rank Fusion -- selectable per query; see DEFAULT_FUSION for the
measurements behind the default and `eval_retrieval.py` to reproduce them.

Config comes from the environment (CLAUDE.md rule 7 -- never hardcode a model
id). The embedding model and endpoint mirror `config/models.yaml`:
    embeddings.primary -> provider: tcs, model: ${TCS_EMBEDDING_MODEL}

Usage:
    python latestchroma.py ingest            # embed + upsert every *.json here
    python latestchroma.py ingest --reset    # drop the collection first
    python latestchroma.py search "kurtosis threshold for bearing spalling"
    python latestchroma.py search "torque spec" --component wheel_assembly
    python latestchroma.py search "PG-M14-6H" --fusion rrf
    python latestchroma.py info              # what is currently indexed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# The build machine sits behind a TLS-intercepting proxy: the corporate root CA
# lives in the Windows trust store, which certifi's bundle does not contain.
# truststore makes httpx/openai read the OS store instead. Never verify=False --
# that would hide a real MITM as readily as it works around this one.
import truststore

# Must run before httpx/openai are imported, because they build their default
# SSL context at import time -- hence the E402 waivers below rather than a
# tidier import block that would silently reintroduce the certificate failure.
truststore.inject_into_ssl()

import chromadb  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from chromadb.api.models.Collection import Collection

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(HERE / ".env")  # folder-local override, if one exists

DB_PATH = str(HERE / "chroma_persist")

TCS_BASE_URL = os.getenv("TCS_BASE_URL", "https://genailab.tcs.in")
TCS_API_KEY = os.getenv("TCS_API_KEY")
EMBEDDING_MODEL = os.getenv(
    "TCS_EMBEDDING_MODEL", "azure/genailab-maas-text-embedding-3-large"
)

EMBED_BATCH_SIZE = 16
RRF_K = 60  # rank-fusion damping constant; 60 is the value from the original RRF paper

# Fusion strategy and weights, chosen by eval_retrieval.py over 28 labelled
# queries in three registers -- document phrasing, colloquial operator phrasing,
# and bare equipment codes. Combined MRR@10 / R@1 / R@3, rounded:
#
#   dense only                     0.87 / 0.82 / 0.93
#   bm25 only                      0.85 / 0.79 / 0.89
#   rrf          (best weighting)  0.88 / 0.82 / 0.96
#   score-normalised, equal weight 0.95 / 0.93 / 1.00   <-- default
#
# Neither half is sufficient alone, and they fail on opposite registers: dense
# wins colloquial phrasing 0.85 vs 0.60, BM25 wins equipment codes 1.00 vs 0.76.
# Fusion beats both, and under the default every query lands in the top 3.
#
# RRF loses to score fusion here because it sees only rank. Asked for
# "PG-M14-6H plug gauge" BM25 puts the right chunk first at 9.89 against a
# runner-up at 2.78 -- overwhelming -- but as "rank 1" that conviction is
# indistinguishable from a marginal win, and a mediocre dense ranking outvoted
# it. Normalising scores keeps the magnitude, and that query moves to rank 1.
#
# Two caveats, both carried from the eval. 28 queries separates these strategies
# (a ~0.07 MRR gap) but cannot certify the exact weight. And figures move by
# ~0.01 between index rebuilds because HNSW construction is randomised, so treat
# the third decimal as noise. The durable claim is score-fusion > RRF > either
# half on mixed traffic, not that 1.0 is the optimal constant.
DEFAULT_FUSION = "score"
DEFAULT_SPARSE_WEIGHT = 1.0

# Chroma collection names must be 3-512 chars of [a-zA-Z0-9._-] and start/end
# alphanumeric. The embedding model id is baked into the name on purpose:
# vectors from two different models are not comparable, so swapping the model
# must not silently reuse an index built by the previous one (models.yaml).
_MODEL_SLUG = re.sub(r"[^a-zA-Z0-9]+", "-", EMBEDDING_MODEL).strip("-").lower()
COLLECTION_NAME = f"forge-knowledge-{_MODEL_SLUG}"[:512]


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def get_embedding_client() -> OpenAI:
    """OpenAI-compatible client pointed at the TCS GenAI Lab gateway.

    Note the base URL carries no /v1 suffix -- the gateway exposes /embeddings
    directly at the root.
    """
    if not TCS_API_KEY:
        raise RuntimeError(
            "TCS_API_KEY is not set. Embeddings cannot be generated.\n"
            f"Expected it in {REPO_ROOT / '.env'} alongside TCS_BASE_URL and "
            "TCS_EMBEDDING_MODEL."
        )
    return OpenAI(api_key=TCS_API_KEY, base_url=TCS_BASE_URL)


def init_chroma(reset: bool = False) -> Collection:
    """Open (or create) the persistent collection and return it."""
    client = chromadb.PersistentClient(path=DB_PATH)

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"  dropped existing collection '{COLLECTION_NAME}'")
        except Exception as exc:  # noqa: BLE001 -- chroma raises bare ValueError here
            # Absent on a first run, which is not an error. Anything else is,
            # so say what happened rather than swallowing it.
            print(f"  no collection to drop ({type(exc).__name__}: {exc})")

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        # Cosine, not Chroma's L2 default: these are normalised text embeddings
        # and cosine is what the model was trained against.
        metadata={"hnsw:space": "cosine"},
    )
    print(f"ChromaDB ready at {DB_PATH}")
    print(f"  collection : {COLLECTION_NAME}")
    print(f"  embeddings : {EMBEDDING_MODEL}")
    print(f"  existing   : {collection.count()} chunks")
    return collection


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
@dataclass
class Chunk:
    """One retrievable unit: a single section of one source document."""

    uid: str  # primary key in Chroma -- globally unique, see build_chunks
    chunk_id: str  # the corpus's own section id, which is NOT unique across files
    text: str  # what gets embedded and BM25-indexed
    metadata: dict[str, Any]


def _flatten_defect_classes(value: Any) -> str:
    """Chroma metadata values must be scalar, so lists are stored comma-joined."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value is not None else ""


def build_chunks(doc: dict[str, Any], source_file: str) -> list[Chunk]:
    """Explode one JSON document into per-section chunks.

    The corpus is already authored in sections with stable `chunk_id`s, so we
    use that structure rather than re-splitting on a character count. Each
    chunk is prefixed with its document context: retrieval matches on a section
    body, but a section body alone does not say which component or station it
    belongs to, and the answer is wrong without that.

    The Chroma primary key is namespaced by source file (`<file>::<chunk_id>`)
    because `chunk_id` is NOT unique across the corpus -- pcb_assembly.json and
    wheel-assembly.json both number their SOPs SOP-WA-1xx while carrying
    entirely different content. Keying on the bare chunk_id would make one file
    silently overwrite 12 documents from the other. The original doc_id and
    chunk_id stay in metadata so citations still resolve.
    """
    doc_id = doc.get("doc_id", "")
    title = doc.get("title", "")
    component_id = doc.get("component_id", "")
    doc_type = doc.get("doc_type", "")
    station = doc.get("station") or ""
    defect_classes = _flatten_defect_classes(doc.get("defect_classes"))
    standard_ref = doc.get("standard_ref") or ""
    version = str(doc.get("version") or "")
    effective_date = doc.get("effective_date") or ""

    chunks: list[Chunk] = []
    for section in doc.get("sections", []):
        heading = section.get("heading", "")
        body = section.get("text", "")
        if not body.strip():
            continue

        chunk_id = section.get("chunk_id") or f"{doc_id}#{len(chunks)}"

        contextual_text = (
            f"Document: {title} ({doc_id} v{version}, {doc_type})\n"
            f"Component: {component_id} | Station: {station}\n"
            f"Defect classes: {defect_classes}\n"
            f"Section: {heading}\n\n"
            f"{body}"
        )

        chunks.append(
            Chunk(
                uid=f"{Path(source_file).stem}::{chunk_id}",
                chunk_id=chunk_id,
                text=contextual_text,
                metadata={
                    "doc_id": doc_id,
                    "title": title,
                    "component_id": component_id,
                    "doc_type": doc_type,
                    "station": station,
                    "defect_classes": defect_classes,
                    "standard_ref": standard_ref,
                    "version": version,
                    "effective_date": effective_date,
                    "heading": heading,
                    "chunk_id": chunk_id,
                    "source_file": source_file,
                    # All FORGE demo data is synthetic and must be labelled as
                    # such everywhere it surfaces (CLAUDE.md, data honesty).
                    "is_synthetic": True,
                },
            )
        )
    return chunks


def load_corpus(folder: Path) -> list[Chunk]:
    """Read every *.json in `folder` and flatten it to chunks."""
    chunks: list[Chunk] = []
    files = sorted(folder.glob("*.json"))
    if not files:
        raise RuntimeError(f"No .json files found in {folder}")

    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        docs = data if isinstance(data, list) else [data]
        file_chunks: list[Chunk] = []
        for doc in docs:
            if not isinstance(doc, dict):
                print(f"  ! {path.name}: skipped a non-object entry", file=sys.stderr)
                continue
            file_chunks.extend(build_chunks(doc, path.name))

        chunks.extend(file_chunks)
        print(f"  {path.name:24} {len(docs):3} docs -> {len(file_chunks):3} chunks")

    _report_id_collisions(chunks)
    return chunks


def _report_id_collisions(chunks: list[Chunk]) -> None:
    """Surface corpus-level chunk_id reuse (CLAUDE.md rule 4 -- no silent failure).

    Namespacing the storage key keeps every chunk retrievable, but a reused
    chunk_id still means two different documents cite the same identifier, so an
    operator following a citation can land on the wrong SOP. That is a data
    defect worth seeing at ingest time, not a warning to bury.
    """
    by_chunk_id: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_chunk_id.setdefault(chunk.chunk_id, []).append(chunk)

    collisions = {k: v for k, v in by_chunk_id.items() if len(v) > 1}
    if not collisions:
        return

    print(
        f"\n  ! {len(collisions)} chunk_id(s) reused across source files.\n"
        "    All copies are indexed under a file-namespaced key, so nothing is\n"
        "    lost, but the corpus reuses these identifiers for different text:"
    )
    for chunk_id, group in sorted(collisions.items()):
        files = ", ".join(c.metadata["source_file"] for c in group)
        print(f"      {chunk_id:22} in {files}")
    print()


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------
@dataclass
class EmbedStats:
    """Provenance for a batch of embedding calls (CLAUDE.md rule 5)."""

    model: str = EMBEDDING_MODEL
    calls: int = 0
    tokens: int = 0
    latency_ms: float = 0.0
    dimensions: int = 0


def embed_texts(
    texts: Sequence[str],
    client: OpenAI | None = None,
    stats: EmbedStats | None = None,
) -> list[Sequence[float]]:
    """Embed `texts` in batches, preserving input order.

    Returns Sequence[float] rather than list[float] because that is what Chroma's
    signature accepts, and list is invariant.
    """
    client = client or get_embedding_client()
    stats = stats if stats is not None else EmbedStats()
    vectors: list[Sequence[float]] = []

    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = list(texts[start : start + EMBED_BATCH_SIZE])
        t0 = time.perf_counter()
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        stats.latency_ms += (time.perf_counter() - t0) * 1000
        stats.calls += 1
        if response.usage:
            stats.tokens += response.usage.total_tokens

        # The API does not guarantee ordering, but it does return an index.
        ordered = sorted(response.data, key=lambda d: d.index)
        vectors.extend(item.embedding for item in ordered)

    if vectors:
        stats.dimensions = len(vectors[0])
    return vectors


def generate_embedding(text: str) -> Sequence[float]:
    """Single-text convenience wrapper."""
    return embed_texts([text])[0]


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
def ingest_chunks(collection: Collection, chunks: list[Chunk]) -> EmbedStats:
    """Embed and upsert chunks.

    Upsert keyed on the file-namespaced uid, so re-running this is idempotent
    instead of accumulating duplicate copies of the corpus.
    """
    if not chunks:
        print("Nothing to ingest.")
        return EmbedStats()

    stats = EmbedStats()
    client = get_embedding_client()

    print(f"\nEmbedding {len(chunks)} chunks via {EMBEDDING_MODEL} ...")
    vectors = embed_texts([c.text for c in chunks], client=client, stats=stats)

    collection.upsert(
        ids=[c.uid for c in chunks],
        documents=[c.text for c in chunks],
        embeddings=vectors,
        metadatas=[c.metadata for c in chunks],
    )

    print(
        f"Ingested {len(chunks)} chunks into '{COLLECTION_NAME}'.\n"
        f"  provenance: model={stats.model} dims={stats.dimensions} "
        f"calls={stats.calls} tokens={stats.tokens} "
        f"latency={stats.latency_ms:.0f}ms"
    )
    return stats


# --------------------------------------------------------------------------
# Hybrid retrieval
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")

# BM25 scores every shared token, so a natural-language question ("how hot is it
# before I shut the motor down") otherwise scores highly against any chunk that
# happens to reuse the same function words. Measured on this corpus: without
# this list the correct chunk for that query fell out of the fused top-3
# entirely, because stopword noise gave unrelated chunks a sparse rank that
# outvoted the dense signal under RRF. Dense retrieval handles the semantics;
# the sparse half only earns its place on the content words.
_STOPWORDS = frozenset(  # noqa: SIM905 -- prose block reads better than a literal
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "there", "here", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "doing", "done", "have", "has", "had", "having", "will", "would", "shall", "should", "can", "could", "may", "might", "must", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your", "his", "its", "our", "their", "of", "in", "on", "at", "to", "for", "from", "by", "with", "without", "into", "onto", "up", "down", "out", "over", "under", "off", "about", "above", "below", "between", "through", "during", "before", "after", "again", "further", "what", "which", "who", "whom", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "too", "very", "just", "also", "as", "get", "gets", "got", "go", "goes", "going", "make", "makes", "made", "use", "used", "using"]
)


def tokenize(text: str) -> list[str]:
    """Lexical tokenizer for BM25.

    Keeps decimals and part numbers intact (`25.6`, `6208-2rs`, `mb-03`), which
    a naive \\w+ split would shatter -- and those tokens are exactly the ones an
    operator searches for. Drops stopwords, which carry no retrieval signal and
    actively mislead BM25 on conversational queries.
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


@dataclass
class SearchHit:
    uid: str  # file-namespaced storage key
    chunk_id: str  # the corpus's own section id, for citation
    document: str
    metadata: dict[str, Any]
    fused_score: float
    fusion: str = DEFAULT_FUSION
    dense_rank: int | None = None
    dense_similarity: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    matched_by: list[str] = field(default_factory=list)


class HybridRetriever:
    """Dense (Chroma HNSW/cosine) + sparse (BM25Okapi) retrieval fused by RRF.

    Dense retrieval catches paraphrase -- "how hot can the bearing get" against
    text that says "thermal limit". BM25 catches exact identifiers -- a part
    number or a clause reference, where an embedding's nearest neighbour is
    often the wrong document that talks about the same topic. Measured on this
    corpus, dense beats BM25 0.854 to 0.598 MRR on conversational queries and
    loses 0.761 to 1.000 on equipment codes; fusing them beats both.

    The two halves are scored on scales that are not comparable, so fusion
    normalises each side before combining (see `search`). The alternative,
    fusing on rank alone, is more robust to scale drift but measurably worse
    here -- it cannot tell an overwhelming lexical match from a marginal one.
    """

    def __init__(self, collection: Collection) -> None:
        self.collection = collection
        self._client = get_embedding_client()

        # The corpus is small (tens of chunks), so the BM25 index is built in
        # memory from the collection at construction time. If this grows past a
        # few thousand chunks, move it to a persisted sparse index.
        record = collection.get(include=["documents", "metadatas"])
        self.ids: list[str] = list(record["ids"])
        self.documents: list[str] = list(record["documents"] or [])
        self.metadatas: list[dict[str, Any]] = [
            dict(m) for m in (record["metadatas"] or [])
        ]

        if not self.ids:
            raise RuntimeError(
                f"Collection '{collection.name}' is empty. "
                "Run `python latestchroma.py ingest` first."
            )

        self._index_by_id = {cid: i for i, cid in enumerate(self.ids)}
        self.bm25 = BM25Okapi([tokenize(d) for d in self.documents])
        # Query embeddings are deterministic for a fixed model, so a repeated
        # query costs one network round trip, not N.
        self._query_vectors: dict[str, Sequence[float]] = {}

    def embed_query(self, query: str) -> Sequence[float]:
        cached = self._query_vectors.get(query)
        if cached is None:
            response = self._client.embeddings.create(
                model=EMBEDDING_MODEL, input=[query]
            )
            cached = response.data[0].embedding
            self._query_vectors[query] = cached
        return cached

    # -- filtering ---------------------------------------------------------
    @staticmethod
    def _build_where(filters: dict[str, str] | None) -> dict[str, Any] | None:
        """Translate simple equality filters into a Chroma `where` clause."""
        active = {k: v for k, v in (filters or {}).items() if v}
        if not active:
            return None
        if len(active) == 1:
            key, value = next(iter(active.items()))
            return {key: value}
        return {"$and": [{k: v} for k, v in active.items()]}

    def _passes_filters(self, idx: int, filters: dict[str, str] | None) -> bool:
        """Apply the same filters to BM25 candidates, which Chroma never saw."""
        active = {k: v for k, v in (filters or {}).items() if v}
        if not active:
            return True
        meta = self.metadatas[idx]
        return all(str(meta.get(k, "")) == v for k, v in active.items())

    # -- the two halves ----------------------------------------------------
    def dense_search(
        self, query: str, top_k: int, filters: dict[str, str] | None = None
    ) -> list[tuple[str, float]]:
        """Vector similarity. Returns (chunk_id, cosine_similarity) ranked best-first."""
        result = self.collection.query(
            query_embeddings=[self.embed_query(query)],
            n_results=min(top_k, len(self.ids)),
            where=self._build_where(filters),
            include=["distances"],
        )
        ids = result["ids"][0]
        raw_distances = result["distances"]
        if raw_distances is None:
            # Only reachable if "distances" is dropped from `include`; without
            # them there is no similarity to fuse on, so fail loudly.
            raise RuntimeError("Chroma returned no distances for a dense query")
        distances = raw_distances[0]
        # Collection is cosine-space, so similarity = 1 - distance.
        return [(cid, 1.0 - dist) for cid, dist in zip(ids, distances, strict=True)]

    def sparse_search(
        self, query: str, top_k: int, filters: dict[str, str] | None = None
    ) -> list[tuple[str, float]]:
        """BM25 lexical match. Returns (chunk_id, bm25_score) ranked best-first."""
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        hits: list[tuple[str, float]] = []
        for idx in ranked:
            if scores[idx] <= 0:
                break  # no lexical overlap at all past this point
            if not self._passes_filters(idx, filters):
                continue
            hits.append((self.ids[idx], float(scores[idx])))
            if len(hits) >= top_k:
                break
        return hits

    # -- fusion ------------------------------------------------------------
    @staticmethod
    def _minmax(scores: dict[str, float]) -> dict[str, float]:
        """Spread scores across [0, 1]. Used for dense.

        Cosine similarities here occupy a narrow band (~0.30-0.55), so the
        absolute values say little and the differences say everything. Min-max
        rescales that band to fill the interval.
        """
        if not scores:
            return {}
        low, high = min(scores.values()), max(scores.values())
        if high - low < 1e-9:
            return dict.fromkeys(scores, 1.0)
        return {k: (v - low) / (high - low) for k, v in scores.items()}

    @staticmethod
    def _maxnorm(scores: dict[str, float]) -> dict[str, float]:
        """Scale scores by the best one. Used for sparse.

        BM25 starts at 0 and is unbounded, so the ratio to the best match is the
        meaningful quantity -- an exact identifier hit scores several times its
        nearest rival and that gap is the signal. Min-max would force the
        weakest candidate to exactly 0 and discard it.
        """
        if not scores:
            return {}
        high = max(scores.values())
        if high < 1e-9:
            return dict.fromkeys(scores, 0.0)
        return {k: v / high for k, v in scores.items()}

    def search(
        self,
        query: str,
        top_k: int = 5,
        candidate_k: int = 20,
        dense_weight: float = 1.0,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        fusion: str = DEFAULT_FUSION,
        filters: dict[str, str] | None = None,
    ) -> list[SearchHit]:
        """Run both retrievers over a wider candidate pool, fuse, return top_k.

        `fusion` selects the strategy:
          "score" -- normalise each retriever's scores and take a weighted sum.
                     Keeps magnitude, so an overwhelming lexical match wins.
          "rrf"   -- Reciprocal Rank Fusion. Uses rank only, so it is immune to
                     score-scale drift, at the cost of flattening confidence.
        """
        if fusion not in ("score", "rrf"):
            raise ValueError(f"unknown fusion strategy {fusion!r}; use 'score' or 'rrf'")

        dense = self.dense_search(query, candidate_k, filters)
        sparse = self.sparse_search(query, candidate_k, filters)

        dense_rank = {cid: r for r, (cid, _) in enumerate(dense, start=1)}
        sparse_rank = {cid: r for r, (cid, _) in enumerate(sparse, start=1)}
        dense_score = dict(dense)
        sparse_score = dict(sparse)

        fused: dict[str, float] = {}
        if fusion == "rrf":
            for cid, rank in dense_rank.items():
                fused[cid] = fused.get(cid, 0.0) + dense_weight / (RRF_K + rank)
            for cid, rank in sparse_rank.items():
                fused[cid] = fused.get(cid, 0.0) + sparse_weight / (RRF_K + rank)
        else:
            for cid, value in self._minmax(dense_score).items():
                fused[cid] = fused.get(cid, 0.0) + dense_weight * value
            for cid, value in self._maxnorm(sparse_score).items():
                fused[cid] = fused.get(cid, 0.0) + sparse_weight * value

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

        hits: list[SearchHit] = []
        for cid, score in ordered:
            idx = self._index_by_id[cid]
            matched_by = []
            if cid in dense_rank:
                matched_by.append("dense")
            if cid in sparse_rank:
                matched_by.append("bm25")

            hits.append(
                SearchHit(
                    uid=cid,
                    chunk_id=str(self.metadatas[idx].get("chunk_id", cid)),
                    document=self.documents[idx],
                    metadata=self.metadatas[idx],
                    fused_score=score,
                    fusion=fusion,
                    dense_rank=dense_rank.get(cid),
                    dense_similarity=dense_score.get(cid),
                    bm25_rank=sparse_rank.get(cid),
                    bm25_score=sparse_score.get(cid),
                    matched_by=matched_by,
                )
            )
        return hits


def hybrid_search(
    query: str,
    top_k: int = 5,
    filters: dict[str, str] | None = None,
    fusion: str = DEFAULT_FUSION,
) -> list[SearchHit]:
    """One-shot hybrid search. Rebuilds the BM25 index per call.

    Fine for a script or a cold path; callers issuing many queries should hold a
    `HybridRetriever` instance instead.
    """
    collection = chromadb.PersistentClient(path=DB_PATH).get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    return HybridRetriever(collection).search(
        query, top_k=top_k, filters=filters, fusion=fusion
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_hits(hits: Iterable[SearchHit], query: str) -> None:
    print(f"\nQuery: {query!r}\n" + "=" * 78)
    empty = True
    for i, hit in enumerate(hits, start=1):
        empty = False
        meta = hit.metadata
        dense = (
            f"#{hit.dense_rank} cos={hit.dense_similarity:.3f}"
            if hit.dense_rank
            else "-"
        )
        sparse = (
            f"#{hit.bm25_rank} bm25={hit.bm25_score:.2f}" if hit.bm25_rank else "-"
        )
        print(f"\n[{i}] {meta.get('doc_id')} -- {meta.get('title')}")
        print(f"    section   : {meta.get('heading')}")
        print(
            f"    component : {meta.get('component_id')} | "
            f"station {meta.get('station')} | {meta.get('doc_type')}"
        )
        print(f"    {hit.fusion}={hit.fused_score:.4f}  dense={dense}  sparse={sparse}")
        print(f"    matched by: {'+'.join(hit.matched_by)}")
        body = hit.document.split("\n\n", 1)[-1].replace("\n", " ")
        print(f"    {body[:220]}...")
    if empty:
        print("\nNo matches.")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="embed and upsert every *.json here")
    p_ingest.add_argument(
        "--reset", action="store_true", help="drop the collection first"
    )

    p_search = sub.add_parser("search", help="hybrid search the collection")
    p_search.add_argument("query")
    p_search.add_argument("-k", "--top-k", type=int, default=5)
    p_search.add_argument("--component", help="filter on component_id")
    p_search.add_argument("--station", help="filter on station")
    p_search.add_argument("--doc-type", help="filter on doc_type (sop|standard_summary)")
    p_search.add_argument(
        "--fusion",
        choices=("score", "rrf"),
        default=DEFAULT_FUSION,
        help=f"rank fusion strategy (default: {DEFAULT_FUSION})",
    )

    sub.add_parser("info", help="show what is indexed")

    args = parser.parse_args(argv)

    if args.command == "ingest":
        collection = init_chroma(reset=args.reset)
        print("\nLoading corpus ...")
        chunks = load_corpus(HERE)
        ingest_chunks(collection, chunks)
        print(f"\nCollection now holds {collection.count()} chunks.")
        return 0

    if args.command == "search":
        filters = {
            "component_id": args.component,
            "station": args.station,
            "doc_type": args.doc_type,
        }
        hits = hybrid_search(
            args.query, top_
            
            args.top_k, filters=filters, fusion=args.fusion
        )
        _print_hits(hits, args.query)
        return 0

    if args.command == "info":
        collection = chromadb.PersistentClient(path=DB_PATH).get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        record = collection.get(include=["metadatas"])
        metas = record["metadatas"] or []
        print(f"Collection : {COLLECTION_NAME}")
        print(f"Path       : {DB_PATH}")
        print(f"Chunks     : {collection.count()}")
        by_component: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for meta in metas:
            by_component[str(meta.get("component_id"))] = (
                by_component.get(str(meta.get("component_id")), 0) + 1
            )
            by_type[str(meta.get("doc_type"))] = (
                by_type.get(str(meta.get("doc_type")), 0) + 1
            )
        print(f"By component: {by_component}")
        print(f"By doc_type : {by_type}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
