"""内容抽取批量试跑、试样报告与字段清洗验收的端到端测试。"""
from __future__ import annotations

import pytest

from app import trial_engine as engine
from app.store import store


@pytest.fixture(autouse=True)
def fresh_registry():
    with engine.trial_registry.lock:
        engine.trial_registry.reset()
        engine.capture_formal_baseline()
    yield
    with engine.trial_registry.lock:
        engine.trial_registry.reset()


def _start(interrupted: bool = False) -> dict:
    return engine.start_run(
        "样本组-测试",
        ["P", "Q"],
        ["news-001", "column-bad", "column-good"],
        interrupted=interrupted,
    )


# ---------------------------------------------------------------------------
# 批量试跑与逐页试样
# ---------------------------------------------------------------------------

def test_batch_run_covers_every_candidate_and_page() -> None:
    result = _start()
    run = result["run"]
    assert run["expected_specimens"] == 6
    assert run["produced_specimens"] == 6
    assert run["complete"] is True

    specimens = engine.trial_registry.specimens[run["run_id"]]
    combos = {(s["candidate_id"], s["page_id"]) for s in specimens}
    assert combos == {(c, p) for c in ("P", "Q")
                      for p in ("news-001", "column-bad", "column-good")}


def test_candidate_p_takes_views_from_news_snapshot() -> None:
    run_id = _start()["run"]["run_id"]
    specimen = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "P" and s["page_id"] == "news-001"
    )
    views = specimen["fields"]["访问量"]
    assert views["raw_value"] == "7"
    assert views["converted_value"] == 7
    assert views["value_type"] == "int"
    assert views["problems"] == []


def test_candidate_p_reports_date_format_problem_without_losing_raw_value() -> None:
    run_id = _start()["run"]["run_id"]
    specimen = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "P" and s["page_id"] == "news-001"
    )
    date_field = specimen["fields"]["发布日期"]
    assert date_field["raw_value"] == "2026/11/21"
    assert date_field["converted_value"] == "2026/11/21"
    assert date_field["value_type"] == "str"
    assert [p["code"] for p in date_field["problems"]] == [engine.PROBLEM_DATE]


def test_candidate_q_date_converts_but_news_area_lacks_field() -> None:
    run_id = _start()["run"]["run_id"]
    specimen = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "Q" and s["page_id"] == "news-001"
    )
    assert specimen["fields"]["发布日期"]["converted_value"] == "2026-11-21"
    assert specimen["fields"]["发布日期"]["value_type"] == "date"
    assert specimen["fields"]["访问量"]["raw_value"] is None
    assert [p["code"] for p in specimen["fields"]["访问量"]["problems"]] == [engine.PROBLEM_MISSING]


def test_bad_date_page_records_conversion_problem() -> None:
    run_id = _start()["run"]["run_id"]
    bad = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "Q" and s["page_id"] == "column-bad"
    )
    good = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "Q" and s["page_id"] == "column-good"
    )
    assert bad["fields"]["发布日期"]["raw_value"] == "2026-13-45"
    assert [p["code"] for p in bad["fields"]["发布日期"]["problems"]] == [engine.PROBLEM_DATE]
    assert good["fields"]["发布日期"]["converted_value"] == "2026-11-22"
    assert good["fields"]["发布日期"]["problems"] == []


def test_report_groups_by_page_and_keeps_value_types() -> None:
    result = _start()
    report = result["report"]
    assert report["page_count"] == 3
    assert report["state_totals"]["未确认"] == 6
    groups = {page["page_group"] for page in report["pages"]}
    assert groups == {"新闻区", "栏目区"}
    news = next(page for page in report["pages"] if page["page_id"] == "news-001")
    p_specimen = next(s for s in news["specimens"] if s["candidate_id"] == "P")
    assert p_specimen["fields"]["访问量"]["value_type"] == "int"
    assert p_specimen["rule_digest"]


def test_specimen_can_be_traced_back_to_rule_and_page_group() -> None:
    run_id = _start()["run"]["run_id"]
    specimen = engine.trial_registry.specimens[run_id][0]
    found = engine.get_specimen(run_id, specimen["trace_id"])
    assert found is not None
    assert found["candidate_snapshot"]["field_mapping"]
    assert found["page_group"]
    assert found["page_snapshot"]["nodes"]
    evaluation = engine._evaluate_specimen(found)
    assert "无法回查候选规则快照" not in evaluation["reasons"]


# ---------------------------------------------------------------------------
# 清洗清单同步
# ---------------------------------------------------------------------------

def test_cleaning_manifest_is_built_from_specimen_raw_values() -> None:
    run_id = _start()["run"]["run_id"]
    manifest = engine.cleaning_manifest(run_id)
    assert manifest is not None
    assert manifest["source"] == "试样报告"
    item = next(i for i in manifest["items"]
                if i["candidate_id"] == "P" and i["page_id"] == "news-001"
                and i["field"] == "访问量")
    assert item["raw_value"] == "7"
    assert item["converted_value"] == 7
    assert item["value_type"] == "int"
    # 该字段在缺访问量的新闻页上会同时出现 null，说明清单保留逐页真实类型
    assert "int" in manifest["field_type_summary"]["访问量"]
    assert "null" in manifest["field_type_summary"]["访问量"]
    assert manifest["formal_writes"] is False


# ---------------------------------------------------------------------------
# 清洗验收：确认前
# ---------------------------------------------------------------------------

def test_acceptance_before_fix_reports_what_it_received() -> None:
    run_id = _start()["run"]["run_id"]
    record = engine.confirm_acceptance(run_id, operator="维护者", note="更正前确认")
    assert record["received_count"] == 6
    assert record["run_state_at_confirm"]["status"] == "已完成"
    eligible = {(t["candidate_id"], t["page_id"]) for t in record["eligible_tests"]}
    assert eligible == {("Q", "column-good")}
    deferred = {(d["candidate_id"], d["page_id"]) for d in record["deferred"]}
    assert ("P", "news-001") in deferred and ("Q", "column-bad") in deferred
    groups = {g["page_group"]: g["status"] for g in record["page_groups"]}
    assert groups == {"新闻区": "仍需补测", "栏目区": "仍需补测"}
    assert record["formal_writes"] is False


def test_acceptance_never_signs_specimen_with_conversion_problem() -> None:
    run_id = _start()["run"]["run_id"]
    record = engine.confirm_acceptance(run_id)
    signed_ids = {t["trace_id"] for t in record["eligible_tests"]}
    for specimen in engine.trial_registry.specimens[run_id]:
        if specimen["trace_id"] in signed_ids:
            assert specimen["problems"] == []
            assert specimen["trace_id"] == engine._evaluate_specimen(specimen)["trace_id"]
        else:
            assert specimen["state"] == engine.STATE_UNCONFIRMED


def test_out_of_scope_candidate_is_not_counted_for_page_group() -> None:
    run_id = _start()["run"]["run_id"]
    record = engine.confirm_acceptance(run_id)
    column = next(g for g in record["page_groups"] if g["page_group"] == "栏目区")
    # P 不适用栏目区，栏目区只按 Q 的两页计资格
    assert column["specimen_count"] == 2
    assert column["out_of_scope_count"] == 2
    assert any(c["code"] == "候选规则越出适用页面组" for c in record["conflicts"])


# ---------------------------------------------------------------------------
# 更正 → 重试 → 再确认
# ---------------------------------------------------------------------------

def test_correcting_page_then_retry_and_confirm_unlocks_group() -> None:
    run_id = _start()["run"]["run_id"]
    engine.confirm_acceptance(run_id, note="更正前确认")

    engine.upsert_snapshot("column-bad", {
        "nodes": {
            "//div[@class='pv']": "12",
            "//span[@class='date']": "2026/11/22",
            "//h1[@class='title']": "栏目坏日期样例（已更正）",
        },
        "operator": "值班人员",
        "note": "坏日期更正",
    })
    retry = engine.retry_run(run_id)
    assert retry["kept_signed"], "已签来源必须保留"
    assert retry["kept_signed"][0]["page_id"] == "column-good"
    replaced = {(item["candidate_id"], item["page_id"]) for item in retry["replaced"]}
    assert ("Q", "column-bad") in replaced
    assert ("Q", "column-good") not in replaced

    record = engine.confirm_acceptance(run_id, note="更正后再确认")
    column = next(g for g in record["page_groups"] if g["page_group"] == "栏目区")
    assert column["status"] == "可交发布验收"


def test_history_keeps_previous_verdict_and_defer_reasons() -> None:
    run_id = _start()["run"]["run_id"]
    first = engine.confirm_acceptance(run_id, note="更正前确认")
    engine.upsert_snapshot("column-bad", {
        "nodes": {
            "//div[@class='pv']": "12",
            "//span[@class='date']": "2026/11/22",
            "//h1[@class='title']": "栏目坏日期样例（已更正）",
        },
    })
    engine.retry_run(run_id)
    engine.confirm_acceptance(run_id, note="更正后确认")

    history = engine.run_history(run_id)
    assert history is not None
    assert len(history["acceptances"]) == 2
    assert history["acceptances"][0]["acceptance_id"] == first["acceptance_id"]
    superseded = [
        entry for entry in history["history"]
        if entry["replaces"] or any(h.get("reason") for h in entry["history"])
    ]
    assert superseded, "被替换的旧试样必须留在历史里"
    old = superseded[0]["history"][0]["specimen"]
    assert old["page_revision"] == 1


def test_retry_marks_corrected_page_and_unchanged_page_differently() -> None:
    run_id = _start()["run"]["run_id"]
    engine.upsert_snapshot("column-bad", {
        "nodes": {
            "//div[@class='pv']": "12",
            "//span[@class='date']": "2026/11/22",
            "//h1[@class='title']": "栏目坏日期样例（已更正）",
        },
    })
    retry = engine.retry_run(run_id)
    kinds = {(item["page_id"]): item["kind"] for item in retry["replaced"]}
    assert kinds["column-bad"] == "页面已更正，重新生成"
    assert kinds["news-001"] == "页面未变，同版本重新生成"

    history = engine.run_history(run_id)
    self_refs = [
        entry for entry in history["history"]
        if entry["replaces"] == entry["trace_id"]
    ]
    assert self_refs == [], "同版本重新生成不应把自己记成被替换来源"


def test_corrected_retry_keeps_original_rule_snapshot() -> None:
    run_id = _start()["run"]["run_id"]
    engine.upsert_snapshot("column-bad", {
        "nodes": {
            "//div[@class='pv']": "12",
            "//span[@class='date']": "2026/11/22",
            "//h1[@class='title']": "栏目坏日期样例（已更正）",
        },
    })
    engine.retry_run(run_id)
    specimen = next(
        s for s in engine.trial_registry.specimens[run_id]
        if s["candidate_id"] == "Q" and s["page_id"] == "column-bad"
    )
    assert specimen["candidate_snapshot"]["date_formats"] == ["%Y/%m/%d"]
    assert specimen["page_revision"] == 2


# ---------------------------------------------------------------------------
# 中断与重试
# ---------------------------------------------------------------------------

def test_interrupted_run_only_replaces_unconfirmed_specimens() -> None:
    result = engine.start_run(
        "样本组-中断",
        ["Q"],
        ["column-good", "column-bad"],
        interrupted=True,
    )
    run = result["run"]
    assert run["status"] == "已中断"
    assert run["produced_specimens"] < run["expected_specimens"]
    assert run["pending_combos"]

    acceptance = engine.confirm_acceptance(run["run_id"])
    signed = {(t["candidate_id"], t["page_id"]) for t in acceptance["eligible_tests"]}
    assert signed

    retry = engine.retry_run(run["run_id"])
    kept = {(item["candidate_id"], item["page_id"]) for item in retry["kept_signed"]}
    assert kept == signed
    assert retry["run"]["complete"] is True
    assert retry["run"]["produced_specimens"] == retry["run"]["expected_specimens"]


def test_retry_preserves_signed_specimen_state() -> None:
    result = _start(interrupted=True)
    run_id = result["run"]["run_id"]
    acceptance = engine.confirm_acceptance(run_id)
    signed_traces = {t["trace_id"] for t in acceptance["eligible_tests"]}
    engine.retry_run(run_id)
    for specimen in engine.trial_registry.specimens[run_id]:
        if specimen["trace_id"] in signed_traces:
            assert specimen["state"] == engine.STATE_SIGNED


# ---------------------------------------------------------------------------
# 正式数据只读
# ---------------------------------------------------------------------------

def test_trial_and_acceptance_do_not_touch_formal_data() -> None:
    baseline = engine.formal_fingerprint()
    run_id = _start()["run"]["run_id"]
    engine.confirm_acceptance(run_id)
    engine.cleaning_manifest(run_id)
    engine.upsert_snapshot("column-bad", {
        "nodes": {
            "//div[@class='pv']": "12",
            "//span[@class='date']": "2026/11/22",
            "//h1[@class='title']": "更正",
        },
    })
    engine.retry_run(run_id)
    engine.confirm_acceptance(run_id)
    unchanged, detail = engine.formal_unchanged()
    assert unchanged, detail
    assert engine.formal_fingerprint() == baseline


def test_formal_clean_rules_still_use_original_rule_set() -> None:
    run_id = _start()["run"]["run_id"]
    before = engine.formal_clean_rules()
    engine.confirm_acceptance(run_id)
    manifest = engine.cleaning_manifest(run_id)
    assert engine.formal_clean_rules() == before
    assert manifest["formal_clean_rules"] == before


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

def test_run_requires_known_candidates_and_pages() -> None:
    with pytest.raises(ValueError):
        engine.start_run("样本组", ["不存在"], ["news-001"])
    with pytest.raises(ValueError):
        engine.start_run("样本组", ["P"], ["不存在"])
    with pytest.raises(ValueError):
        engine.start_run("", ["P"], ["news-001"])


def test_scenario_replays_full_story() -> None:
    result = engine.scenario()
    assert result["formal_unchanged"] is True
    assert result["acceptance_before_fix"]["received_count"] == 6
    assert result["acceptance_after_fix"]["received_count"] == 6
    assert result["retry"]["kept_signed"]
    assert result["interrupted_run"]["retry"]["kept_signed"]
    assert result["formal_clean_rules"]
