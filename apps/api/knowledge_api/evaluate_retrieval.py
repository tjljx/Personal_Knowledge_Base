"""Evaluate retrieval against a manually labeled JSONL question set."""

import argparse
import json
import time
from pathlib import Path

from sqlalchemy.orm import Session

from knowledge_api.db import get_engine
from knowledge_api.main import retrieval_terms
from knowledge_api.retrieval import retrieve_chunks


def evaluate(db: Session, kb_id: str, dataset: Path) -> dict:
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line]
    if not cases:
        raise ValueError("评测集不能为空")
    totals = {
        mode: {
            "hits": 0,
            "chunk_hits": 0,
            "document_hits": 0,
            "refusals": 0,
            "full_chain_hits": 0,
            "graph_evidence_hits": 0,
            "misses": [],
        }
        for mode in ("baseline", "with_graph")
    }
    answerable = unanswerable = full_chain_cases = graph_evidence_cases = 0
    chunk_cases = document_cases = 0
    graph_channel_chunks = graph_only_chunks = graph_new_hits = 0
    tags: dict[str, dict[str, list[int]]] = {}
    durations: dict[str, list[float]] = {"baseline": [], "with_graph": []}
    case_diagnostics = []
    for case in cases:
        question = case["question"]
        expected_chunks = set(case.get("expected_chunk_ids", []))
        expected_documents = set(case.get("expected_document_ids", []))
        expected_evidence = set(case.get("expected_graph_evidence_ids", []))
        if not case.get("should_refuse", False) and not (expected_chunks or expected_documents):
            raise ValueError(f"缺少期望文档或片段：{question}")
        trace: dict = {}
        terms = retrieval_terms(question)
        rows_by_mode = {}
        stages_by_mode = {}
        for mode in ("baseline", "with_graph"):
            started = time.perf_counter()
            stages = {}
            rows_by_mode[mode] = retrieve_chunks(
                db,
                kb_id,
                question,
                terms,
                [],
                limit=10,
                include_graph=mode == "with_graph",
                trace=trace if mode == "with_graph" else None,
                diagnostics=stages,
            )
            stages_by_mode[mode] = stages
            durations[mode].append((time.perf_counter() - started) * 1000)
        if case.get("should_refuse", False):
            unanswerable += 1
        else:
            answerable += 1
            chunk_cases += bool(expected_chunks)
            document_cases += bool(expected_documents)
            full_chain_cases += len(expected_documents) > 1
            graph_evidence_cases += bool(expected_evidence)
        hit_by_mode = {}
        for mode, rows in rows_by_mode.items():
            found_chunks = {row[2].id for row in rows}
            found_documents = {row[0].id for row in rows}
            hit = bool(expected_chunks & found_chunks or expected_documents & found_documents)
            hit_by_mode[mode] = hit
            if case.get("should_refuse", False):
                totals[mode]["refusals"] += not rows
                if rows:
                    totals[mode]["misses"].append(question)
            else:
                totals[mode]["hits"] += hit
                totals[mode]["chunk_hits"] += bool(expected_chunks & found_chunks)
                totals[mode]["document_hits"] += bool(expected_documents & found_documents)
                totals[mode]["full_chain_hits"] += (
                    len(expected_documents) > 1 and expected_documents <= found_documents
                )
                if not hit:
                    totals[mode]["misses"].append(question)
            if mode == "with_graph":
                found_evidence = {
                    item["id"] for record in trace.values() for item in record["graph_evidence"]
                }
                totals[mode]["graph_evidence_hits"] += bool(expected_evidence & found_evidence)
        graph_new_hits += hit_by_mode["with_graph"] and not hit_by_mode["baseline"]
        graph_channel_chunks += sum("graph" in item["channels"] for item in trace.values())
        graph_only_chunks += sum(item["channels"] == ["graph"] for item in trace.values())
        stage_names = ("keyword", "vector", "graph", "fused", "reranked", "final")
        case_result = {"question": question, "expected_chunk_ids": sorted(expected_chunks)}
        for mode, stages in stages_by_mode.items():
            ranks = {
                stage: min(
                    (
                        index
                        for index, chunk_id in enumerate(stages.get(stage, []), 1)
                        if chunk_id in expected_chunks
                    ),
                    default=None,
                )
                for stage in stage_names
            }
            if not expected_chunks:
                cause = "no_chunk_label"
            elif ranks["final"] is not None:
                cause = "hit"
            elif ranks["reranked"] is not None:
                cause = "top_k_cutoff"
            elif ranks["fused"] is not None:
                cause = "candidate_pool_cutoff"
            else:
                cause = "no_channel_candidate"
            case_result[mode] = {
                "expected_chunk_ranks": ranks,
                "chunk_outcome": cause,
                "reranker_applied": stages.get("reranker_applied", False),
                "graph_details": stages.get("graph_details", {}),
            }
        case_diagnostics.append(case_result)
        for tag in case.get("tags", []):
            bucket = tags.setdefault(tag, {"baseline": [], "with_graph": []})
            for mode in rows_by_mode:
                if not case.get("should_refuse", False):
                    bucket[mode].append(int(hit_by_mode[mode]))

    def summary(mode: str) -> dict:
        result = totals[mode]
        return {
            "recall_at_10": round(result["hits"] / answerable, 4) if answerable else None,
            "chunk_recall_at_10": round(result["chunk_hits"] / chunk_cases, 4)
            if chunk_cases
            else None,
            "document_recall_at_10": round(result["document_hits"] / document_cases, 4)
            if document_cases
            else None,
            "empty_retrieval_rate": round(result["refusals"] / unanswerable, 4)
            if unanswerable
            else None,
            "full_chain_hit_rate": round(result["full_chain_hits"] / full_chain_cases, 4)
            if full_chain_cases
            else None,
            "graph_evidence_hit_rate": round(
                result["graph_evidence_hits"] / graph_evidence_cases, 4
            )
            if graph_evidence_cases and mode == "with_graph"
            else None,
            "average_retrieval_ms": round(sum(durations[mode]) / len(durations[mode]), 1),
            "missed_questions": result["misses"],
        }

    baseline = summary("baseline")
    with_graph = summary("with_graph")
    return {
        "cases": len(cases),
        "answerable": answerable,
        "unanswerable": unanswerable,
        **with_graph,
        "baseline": baseline,
        "with_graph": with_graph,
        "graph_new_hits": graph_new_hits,
        "graph_channel_chunks_in_top10": graph_channel_chunks,
        "graph_only_chunks_in_top10": graph_only_chunks,
        "by_tag": {
            tag: {
                mode: {"cases": len(values), "recall_at_10": round(sum(values) / len(values), 4)}
                for mode, values in buckets.items()
                if values
            }
            for tag, buckets in tags.items()
        },
        "case_diagnostics": case_diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate KB retrieval with labeled JSONL questions"
    )
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    with Session(get_engine()) as db:
        print(json.dumps(evaluate(db, args.kb_id, args.dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
