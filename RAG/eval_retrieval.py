"""Retrieval evaluation for the FORGE knowledge index.

Compares dense-only, BM25-only, and both hybrid fusion strategies on three
hand-labelled query sets, and sweeps the sparse weight. Run it after
`latestchroma.py ingest` to justify the retriever's defaults with a measurement
instead of an assertion.

    python eval_retrieval.py

The three sets exist because a single set hid the answer. GOLDEN is phrased the
way the documents are written and every configuration scores a perfect 1.000 on
it -- it cannot tell any two retrievers apart. HARD asks the same questions in
an operator's words, where dense wins. IDENTIFIER is bare equipment codes, where
BM25 wins outright and dense is at its worst. Only across all three does the
case for fusion appear, and it appears because the two halves fail on opposite
registers rather than because either is better overall.

Honest caveats, because a number without its caveats is worse than no number:
  * 28 queries total. Enough to separate these strategies (the gap between best
    and worst is ~0.11 MRR), not enough to certify a specific weight.
  * Figures move ~0.01 between index rebuilds -- HNSW construction is
    randomised. The third decimal is noise.
  * Ground truth was labelled by reading the corpus and the queries were written
    against it, so real operator phrasing will differ and will be harder.
  * The winning configuration is selected on this same set, so its reported
    figure is optimistic. This is a sanity check, not a benchmark result.
"""

from __future__ import annotations

import chromadb
from latestchroma import COLLECTION_NAME, DB_PATH, HybridRetriever

# (query, {acceptable chunk uids}) -- a set, because several sections can
# legitimately answer the same question. Every target below was confirmed by
# reading the section text, not inferred from its heading.
GOLDEN_SET: list[tuple[str, set[str]]] = [
    (
        "at what housing temperature must I shut the motor down",
        {"motor-bearing::SOP-MB-102#2.0"},
    ),
    (
        "how many grams of grease does the bearing need",
        {"motor-bearing::SOP-MB-107#1.0"},
    ),
    (
        "the fastener was tightened past yield, what do I do now",
        {"wheel-assembly::SOP-WA-108#3.2"},
    ),
    (
        "final torque came out below spec, how do I diagnose it",
        {"wheel-assembly::SOP-WA-109#2.0"},
    ),
    (
        "ball pass frequency outer race for a 6208 bearing",
        {"motor-bearing::SOP-MB-106#1.0"},
    ),
    (
        "measuring how much the shaft wobbles off true",
        {"motor-bearing::SOP-MB-104#2.0"},
    ),
    (
        "permissible vibration velocity limits for a 30 kW motor",
        {"motor-bearing::STD-MB-201#1.0"},
    ),
    (
        "what kurtosis value signals an incipient bearing fault",
        {"motor-bearing::SOP-MB-101#2.0", "motor-bearing::SOP-MB-108#2.0"},
    ),
    (
        "bolt went in crooked and damaged the threads",
        {"wheel-assembly::SOP-WA-107#1.4", "pcb_assembly::SOP-WA-104#1.0"},
    ),
    (
        "TPMS sensor did not respond to the trigger",
        {"pcb_assembly::SOP-WA-111#1.0", "wheel-assembly::SOP-WA-104#1.1"},
    ),
]

# The set above is phrased the way a document is written, and every retriever
# configuration scores a perfect 1.000 on it -- with 48 chunks and a 3072-dim
# model it does not discriminate. These are the same questions in the register
# an operator actually uses at the station: colloquial, no domain vocabulary,
# no identifiers. This is where the fusion weight starts to matter.
HARD_SET: list[tuple[str, set[str]]] = [
    (
        "how hot is too hot before I shut the motor down",
        {"motor-bearing::SOP-MB-102#2.0"},
    ),
    ("how much grease goes in", {"motor-bearing::SOP-MB-107#1.0"}),
    ("we cranked it way too tight", {"wheel-assembly::SOP-WA-108#3.2"}),
    ("the bolt is not tight enough", {"wheel-assembly::SOP-WA-109#2.0"}),
    (
        "the grease looks dark and dirty",
        {"motor-bearing::SOP-MB-103#2.0"},
    ),
    ("oil is weeping out of the seal", {"motor-bearing::SOP-MB-103#1.0"}),
    (
        "what do I write down at the end of my shift",
        {"motor-bearing::SOP-MB-114#1.0", "wheel-assembly::SOP-WA-111#5.1"},
    ),
    (
        "there are scratches on the wheel face",
        {"pcb_assembly::SOP-WA-107#1.0", "wheel-assembly::SOP-WA-110#1.1"},
    ),
]

# Equipment and procedure identifiers, each verified to occur in exactly one
# chunk. This is the register the sparse half exists for: an operator holding a
# gauge reads the code stamped on it and types that. An embedding places a rare
# alphanumeric token near other rare alphanumeric tokens, which is the wrong
# neighbourhood -- lexical match is exact by construction.
IDENTIFIER_SET: list[tuple[str, set[str]]] = [
    ("PG-M14-6H plug gauge", {"wheel-assembly::SOP-WA-108#3.2"}),
    ("GRE-GUN-01", {"motor-bearing::SOP-MB-107#1.0"}),
    ("IR-CAM-02 camera", {"motor-bearing::SOP-MB-102#1.0"}),
    ("MB-LOTO-01 procedure", {"motor-bearing::SOP-MB-103#1.0"}),
    ("SOL-404 solvent", {"wheel-assembly::SOP-WA-106#2.3"}),
    ("QF-809 form", {"pcb_assembly::SOP-WA-106#1.0"}),
    ("CAL-HUB-120", {"wheel-assembly::SOP-WA-101#1.0"}),
    ("DAQ-MB-01 module", {"motor-bearing::SOP-MB-101#1.0"}),
    ("ALIGN-TOUCH-01", {"motor-bearing::SOP-MB-104#1.0"}),
    ("MIC-LAB-01", {"motor-bearing::SOP-MB-113#1.0"}),
]

CUTOFF = 10


def _best_rank(ranked_ids: list[str], relevant: set[str]) -> int | None:
    """1-based rank of the first relevant id, or None if absent."""
    for rank, cid in enumerate(ranked_ids, start=1):
        if cid in relevant:
            return rank
    return None


def score(runs: list[tuple[list[str], set[str]]]) -> tuple[float, float, float]:
    """Return (MRR@10, recall@1, recall@3)."""
    rr = hit1 = hit3 = 0.0
    for ranked, relevant in runs:
        rank = _best_rank(ranked[:CUTOFF], relevant)
        if rank:
            rr += 1.0 / rank
            hit1 += rank <= 1
            hit3 += rank <= 3
    n = len(runs)
    return rr / n, hit1 / n, hit3 / n


def main() -> int:
    collection = chromadb.PersistentClient(path=DB_PATH).get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    retriever = HybridRetriever(collection)

    # (label, mode, fusion, sparse_weight)
    configs: list[tuple[str, str, str, float]] = [
        ("dense only", "dense", "", 0.0),
        ("bm25 only", "sparse", "", 0.0),
        ("rrf sparse=0.25", "hybrid", "rrf", 0.25),
        ("rrf sparse=1.00", "hybrid", "rrf", 1.00),
        ("score sparse=0.50", "hybrid", "score", 0.50),
        ("score sparse=0.75", "hybrid", "score", 0.75),
        ("score sparse=1.00", "hybrid", "score", 1.00),
    ]

    def run_config(
        mode: str, fusion: str, weight: float, queries: list[tuple[str, set[str]]]
    ) -> tuple[float, float, float]:
        runs: list[tuple[list[str], set[str]]] = []
        for query, relevant in queries:
            if mode == "dense":
                ranked = [cid for cid, _ in retriever.dense_search(query, CUTOFF)]
            elif mode == "sparse":
                ranked = [cid for cid, _ in retriever.sparse_search(query, CUTOFF)]
            else:
                hits = retriever.search(
                    query,
                    top_k=CUTOFF,
                    candidate_k=20,
                    sparse_weight=weight,
                    fusion=fusion,
                )
                ranked = [h.uid for h in hits]
            runs.append((ranked, relevant))
        return score(runs)

    print(f"Corpus: {len(retriever.ids)} chunks")
    print(f"Metrics @ cutoff {CUTOFF}\n")

    all_sets = (
        ("GOLDEN (document register)", GOLDEN_SET),
        ("HARD (operator register)", HARD_SET),
        ("IDENTIFIER (equipment codes)", IDENTIFIER_SET),
    )
    combined = [q for _, qs in all_sets for q in qs]

    for set_name, queries in all_sets:
        print(f"{set_name} -- {len(queries)} queries")
        print(f"  {'config':22} {'MRR@10':>8} {'R@1':>7} {'R@3':>7}")
        print("  " + "-" * 47)
        results: list[tuple[str, float, float, float]] = []
        for label, mode, fusion, weight in configs:
            mrr, r1, r3 = run_config(mode, fusion, weight, queries)
            results.append((label, mrr, r1, r3))
            print(f"  {label:22} {mrr:8.3f} {r1:7.2f} {r3:7.2f}")
        spread = max(r[1] for r in results) - min(r[1] for r in results)
        best = max(results, key=lambda r: (r[1], r[3]))
        if spread < 0.02:
            print(f"  -> no separation (MRR spread {spread:.3f}); "
                  "this set cannot choose a config\n")
        else:
            print(f"  -> best: {best[0]} (MRR@10 {best[1]:.3f}, R@3 {best[3]:.2f})\n")

    print(f"COMBINED -- {len(combined)} queries")
    print(f"  {'config':22} {'MRR@10':>8} {'R@1':>7} {'R@3':>7}")
    print("  " + "-" * 47)
    combined_results: list[tuple[str, float, float, float]] = []
    for label, mode, fusion, weight in configs:
        mrr, r1, r3 = run_config(mode, fusion, weight, combined)
        combined_results.append((label, mrr, r1, r3))
        print(f"  {label:22} {mrr:8.3f} {r1:7.2f} {r3:7.2f}")
    best = max(combined_results, key=lambda r: (r[1], r[3]))
    print(f"  -> best overall: {best[0]} (MRR@10 {best[1]:.3f}, R@3 {best[3]:.2f})\n")

    print("Per-query rank under the current default (misses and low ranks first):")
    rows: list[tuple[int, str, str]] = []
    for set_name, queries in all_sets:
        for query, relevant in queries:
            hits = retriever.search(query, top_k=CUTOFF)
            rank = _best_rank([h.uid for h in hits], relevant)
            rows.append((rank or 999, set_name.split()[0], query))
    for rank, tag, query in sorted(rows, reverse=True):
        flag = " " if rank <= 3 else "!"
        shown = "MISS" if rank == 999 else str(rank)
        print(f"  {flag} rank={shown:>4}  [{tag:10}] {query[:52]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
