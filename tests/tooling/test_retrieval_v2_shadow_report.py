import json

import pytest

from hina_bot.tooling.retrieval_v2_shadow_report import build_report, main


def row(**overrides):
    value = {
        "operation": "retrieval_v2.shadow",
        "status": "completed",
        "retrieval_v2_mode": "shadow",
        "retrieval_v2_overlap_rate": 0.5,
        "retrieval_v2_elapsed_ms": 20,
        "retrieval_v2_zero_result": False,
        "retrieval_v2_relation_hit": True,
        "retrieval_v2_evidence_sufficient": True,
        "retrieval_v2_selected_factual": 2,
        "retrieval_v2_selected_relation": 1,
        "retrieval_v2_selected_ambient": 0,
        "retrieval_v2_selected_reaction": 0,
        "retrieval_v2_factual_lexical_selected": 1,
        "retrieval_v2_factual_semantic_selected": 1,
        "retrieval_v2_embedding_prompt_tokens": 15,
        "retrieval_v2_embedding_requests": 1,
        "retrieval_v2_cache_hits": 8,
        "retrieval_v2_cache_misses": 2,
        "retrieval_v2_calibration_status": "configured",
        "retrieval_v2_factual_invocation": "semantic_enabled",
        "retrieval_v2_evidence_reason": "matched_direct_claim",
    }
    value.update(overrides)
    return value


def test_shadow_report_aggregates_only_content_free_retrieval_rows():
    report = build_report([
        row(),
        row(
            operation="retrieval_v2.active",
            retrieval_v2_mode="active",
            retrieval_v2_overlap_rate=1.0,
            retrieval_v2_elapsed_ms=30,
            retrieval_v2_zero_result=True,
            retrieval_v2_relation_hit=False,
            retrieval_v2_evidence_sufficient=False,
            retrieval_v2_embedding_prompt_tokens=5,
            retrieval_v2_cache_hits=1,
            retrieval_v2_cache_misses=0,
        ),
        {"operation": "answer", "status": "completed", "secret": "ignored"},
        {
            "operation": "retrieval_v2.shadow",
            "status": "skipped",
            "retrieval_v2_mode": "shadow",
            "retrieval_v2_fallback_reason": "shadow_busy",
        },
    ])

    assert report["turns"] == 3
    assert report["completed"] == 2
    assert report["status_counts"] == {"completed": 2, "skipped": 1}
    assert report["mean_overlap_rate"] == pytest.approx(0.75)
    assert report["mean_elapsed_ms"] == pytest.approx(25)
    assert report["zero_result_rate"] == pytest.approx(0.5)
    assert report["relation_hit_rate"] == pytest.approx(0.5)
    assert report["embedding"]["prompt_tokens"] == 20
    assert report["embedding"]["requests"] == 2
    assert report["embedding"]["cache_hit_rate"] == pytest.approx(0.8182)
    assert report["fallback_reasons"] == {"shadow_busy": 1}


def test_shadow_report_cli_ignores_malformed_and_non_retrieval_rows(tmp_path, capsys):
    path = tmp_path / "usage.jsonl"
    path.write_text(
        json.dumps(row(), ensure_ascii=False)
        + "\nnot-json\n"
        + json.dumps({"operation": "answer", "secret": "ignored"})
        + "\n",
        encoding="utf-8",
    )

    assert main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["turns"] == 1
    assert report["completed"] == 1
