"""内容抽取试跑 / 试样报告 / 字段清洗验收接口。

这些接口只读写试跑登记表，不会落到内容抽取、字段清洗、批次存储等正式数据。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import ActionResult
from app.services import trial as trial_service
from app.services.trial import TrialError

router = APIRouter(prefix="/api/trial", tags=["抽取试跑与清洗验收"])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=what)


@router.get("/state", response_model=dict)
def get_state() -> dict[str, Any]:
    return trial_service.state()


@router.get("/candidates", response_model=list[dict])
def list_candidates() -> list[dict[str, Any]]:
    return trial_service.candidates()


@router.post("/candidates", response_model=ActionResult)
def create_candidate(payload: dict[str, Any]) -> ActionResult:
    try:
        candidate = trial_service.create_candidate(payload)
    except TrialError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message="候选规则已登记", entry=candidate)


@router.get("/snapshots", response_model=list[dict])
def list_snapshots() -> list[dict[str, Any]]:
    return trial_service.snapshots()


@router.put("/snapshots/{page_id}", response_model=ActionResult)
def upsert_snapshot(page_id: str, payload: dict[str, Any]) -> ActionResult:
    try:
        snapshot = trial_service.upsert_snapshot(page_id, payload)
    except TrialError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=f"页面 {page_id} 快照已更新", entry=snapshot)


@router.post("/runs", response_model=ActionResult)
def create_run(payload: dict[str, Any]) -> ActionResult:
    try:
        result = trial_service.start_run(payload)
    except TrialError as exc:
        return ActionResult(ok=False, message=str(exc))
    run = result["run"]
    return ActionResult(
        ok=True,
        message=f"批量试跑 {run['run_id']} 已生成 {run['produced_specimens']}/{run['expected_specimens']} 份试样",
        entry=result,
    )


@router.get("/runs", response_model=list[dict])
def list_runs() -> list[dict[str, Any]]:
    return trial_service.runs()


@router.get("/runs/{run_id}/report", response_model=dict)
def get_report(run_id: str) -> dict[str, Any]:
    report = trial_service.report(run_id)
    if report is None:
        raise _not_found(f"试跑批次 {run_id} 不存在")
    return report


@router.post("/runs/{run_id}/retry", response_model=ActionResult)
def retry_run(run_id: str, payload: dict[str, Any] | None = None) -> ActionResult:
    try:
        result = trial_service.retry(run_id, payload)
    except TrialError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(
        ok=True,
        message=(f"试跑 {run_id} 已重试：替换 {len(result['replaced'])} 份未确认试样，"
                 f"保留 {len(result['kept_signed'])} 份已签试样"),
        entry=result,
    )


@router.get("/runs/{run_id}/cleaning-manifest", response_model=dict)
def get_cleaning_manifest(run_id: str) -> dict[str, Any]:
    manifest = trial_service.manifest(run_id)
    if manifest is None:
        raise _not_found(f"试跑批次 {run_id} 不存在")
    return manifest


@router.get("/runs/{run_id}/history", response_model=dict)
def get_history(run_id: str) -> dict[str, Any]:
    history = trial_service.history(run_id)
    if history is None:
        raise _not_found(f"试跑批次 {run_id} 不存在")
    return history


@router.post("/runs/{run_id}/acceptances", response_model=ActionResult)
def confirm_acceptance(run_id: str, payload: dict[str, Any]) -> ActionResult:
    try:
        record = trial_service.confirm(run_id, payload)
    except TrialError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(
        ok=True,
        message=(f"验收 {record['acceptance_id']} 已登记：领取 {record['received_count']} 份试样，"
                 f"可交发布验收 {len(record['eligible_tests'])} 条，暂缓 {len(record['deferred'])} 份"),
        entry=record,
    )


@router.get("/acceptances", response_model=list[dict])
def list_acceptances(run_id: str | None = None) -> list[dict[str, Any]]:
    return trial_service.acceptances(run_id)


@router.get("/acceptances/{acceptance_id}", response_model=dict)
def get_acceptance(acceptance_id: str) -> dict[str, Any]:
    record = trial_service.acceptance(acceptance_id)
    if record is None:
        raise _not_found(f"验收记录 {acceptance_id} 不存在")
    return record


@router.get("/specimens/{specimen_id}", response_model=dict)
def get_specimen(specimen_id: str, run_id: str | None = None) -> dict[str, Any]:
    specimen = trial_service.specimen(specimen_id, run_id)
    if specimen is None:
        raise _not_found(f"试样 {specimen_id} 不存在")
    return specimen


@router.post("/scenario", response_model=dict)
def replay_scenario() -> dict[str, Any]:
    """重放题面示例：坏日期更正前确认 → 更正 → 重试 → 再确认，全部可回查。"""
    return trial_service.scenario()
