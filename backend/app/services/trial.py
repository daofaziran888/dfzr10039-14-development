"""内容抽取试跑与字段清洗验收的服务层：把引擎能力包成路由可直接调用的返回结构。"""
from __future__ import annotations

from typing import Any

from app import trial_engine as engine


class TrialError(Exception):
    """可预期的业务错误，路由层转成 400 响应。"""


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        raise TrialError(str(exc)) from exc


def state() -> dict[str, Any]:
    """试跑登记表概览 + 正式数据只读证明。"""
    unchanged, detail = engine.formal_unchanged()
    return {
        "candidates": engine.list_candidates(),
        "snapshots": engine.list_snapshots(),
        "runs": engine.list_runs(),
        "acceptances": engine.list_acceptances(),
        "formal_clean_rules": engine.formal_clean_rules(),
        "formal_unchanged": unchanged,
        "formal_detail": detail,
        "trial_writes_formal": False,
    }


def candidates() -> list[dict[str, Any]]:
    return engine.list_candidates()


def create_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    return _call(engine.register_candidate, payload)


def snapshots() -> list[dict[str, Any]]:
    return engine.list_snapshots()


def upsert_snapshot(page_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _call(engine.upsert_snapshot, page_id, payload)


def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    return _call(
        engine.start_run,
        payload.get("sample_group", ""),
        payload.get("candidate_ids", []),
        payload.get("page_ids", []),
        interrupted=bool(payload.get("interrupted")),
        operator=str(payload.get("operator") or "运营"),
    )


def runs() -> list[dict[str, Any]]:
    return engine.list_runs()


def report(run_id: str) -> dict[str, Any] | None:
    return engine.run_report(run_id)


def retry(run_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    return _call(engine.retry_run, run_id, operator=str(payload.get("operator") or "运营"))


def manifest(run_id: str) -> dict[str, Any] | None:
    return engine.cleaning_manifest(run_id)


def history(run_id: str) -> dict[str, Any] | None:
    return engine.run_history(run_id)


def confirm(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _call(
        engine.confirm_acceptance,
        run_id,
        operator=str(payload.get("operator") or "维护者"),
        note=str(payload.get("note") or ""),
        specimen_ids=payload.get("specimen_ids"),
    )


def acceptances(run_id: str | None = None) -> list[dict[str, Any]]:
    return engine.list_acceptances(run_id)


def acceptance(acceptance_id: str) -> dict[str, Any] | None:
    return engine.get_acceptance(acceptance_id)


def specimen(specimen_id: str, run_id: str | None = None) -> dict[str, Any] | None:
    if run_id:
        return engine.get_specimen(run_id, specimen_id)
    for run in engine.list_runs():
        found = engine.get_specimen(run["run_id"], specimen_id)
        if found is not None:
            return found
    return None


def scenario() -> dict[str, Any]:
    return engine.scenario()
