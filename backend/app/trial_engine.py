"""候选抽取规则批量试跑、试样报告与字段清洗验收的领域引擎。

业务位置
--------
内容抽取模块原先只有规则的增删改查，没有办法拿本地网页采集快照对候选规则
做“先试后用”。本引擎提供：

* 一次提交同组样本（``sample_group``）的批量试跑：每条候选规则 × 每个网页
  都生成一份试样，逐页保留原值、转换值和转换问题；
* 试样报告按网页 / 页面组 / 候选规则回查，试跑结果同步成字段清洗清单；
* 字段清洗端基于试样报告发起验收，明确列出“实际领到了哪些试样”、
  “哪些测试条目可以交给发布验收”、“哪些页面组还没资格”，并保存每次确认
  的历史快照，更正页面后仍能回查此前的暂缓原因。

关键约束
--------
1. 试跑与清洗抽验只写本模块自己的登记表，绝不改动内容抽取、字段清洗、
   批次存储等正式数据；
2. 每份试样必须记录候选规则 + 适用页面组 + 页面 + 原始值，缺任何一项都
   不能被签为通过；
3. 批量试跑中断后重试，只替换本次未确认的试样，已经签过的来源原样保留。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

STATE_UNCONFIRMED = "未确认"
STATE_SIGNED = "已签"

PROBLEM_MISSING = "缺字段"
PROBLEM_CAST = "类型转换失败"
PROBLEM_DATE = "日期格式异常"
PROBLEM_RULE = "候选规则不可回查"
PROBLEM_PAGE_GROUP = "页面组不适用"

TYPE_ALIASES = {
    "int": "int", "integer": "int", "整数": "int", "整型": "int",
    "float": "float", "double": "float", "number": "float",
    "小数": "float", "浮点": "float",
    "date": "date", "日期": "date",
    "datetime": "datetime", "日期时间": "datetime", "时间": "datetime",
    "bool": "bool", "boolean": "bool", "布尔": "bool",
    "str": "string", "string": "string", "text": "string",
    "字符串": "string", "文本": "string",
}

TYPE_LABELS = {
    "int": "int", "float": "float", "date": "date", "datetime": "datetime",
    "bool": "bool", "string": "str",
}

DEFAULT_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]
DEFAULT_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_type(raw: Any) -> str:
    """把规则里五花八门的类型写法归一到内部类型。"""
    text = str(raw or "string").strip().lower()
    return TYPE_ALIASES.get(text, "string")


def _problem(code: str, message: str | None = None) -> dict[str, str]:
    return {"code": code, "message": message or code}


def convert_value(raw_value: Any, type_name: Any,
                  date_formats: list[str] | None = None) -> dict[str, Any]:
    """按声明类型对原始文本做一次真实的 Python 类型转换。

    返回 ``converted_value`` / ``value_type`` / ``problems``，转换失败时保留
    原值并把问题写清楚，绝不吞掉异常。
    """
    target = canonical_type(type_name)
    if raw_value is None or str(raw_value).strip() == "":
        return {
            "converted_value": None,
            "value_type": "null",
            "problems": [_problem(PROBLEM_MISSING, "页面未取到该字段")],
        }

    text = str(raw_value)
    stripped = text.strip()
    problems: list[dict[str, str]] = []

    if target == "int":
        try:
            value = int(stripped.replace(",", ""))
        except (TypeError, ValueError):
            return {
                "converted_value": text,
                "value_type": "str",
                "problems": [_problem(PROBLEM_CAST, f"无法把 {text!r} 转成 int")],
            }
        return {"converted_value": value, "value_type": TYPE_LABELS["int"], "problems": []}

    if target == "float":
        try:
            value = float(stripped.replace(",", ""))
        except (TypeError, ValueError):
            return {
                "converted_value": text,
                "value_type": "str",
                "problems": [_problem(PROBLEM_CAST, f"无法把 {text!r} 转成 float")],
            }
        return {"converted_value": value, "value_type": TYPE_LABELS["float"], "problems": []}

    if target == "bool":
        lowered = stripped.lower()
        if lowered in {"true", "1", "yes", "y", "是"}:
            return {"converted_value": True, "value_type": "bool", "problems": []}
        if lowered in {"false", "0", "no", "n", "否"}:
            return {"converted_value": False, "value_type": "bool", "problems": []}
        return {
            "converted_value": text,
            "value_type": "str",
            "problems": [_problem(PROBLEM_CAST, f"无法把 {text!r} 转成 bool")],
        }

    if target in {"date", "datetime"}:
        formats = date_formats or (
            DEFAULT_DATE_FORMATS if target == "date" else DEFAULT_DATETIME_FORMATS
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(stripped, fmt)
            except (TypeError, ValueError):
                continue
            if target == "date":
                return {
                    "converted_value": parsed.date().isoformat(),
                    "value_type": TYPE_LABELS["date"],
                    "problems": [],
                }
            return {
                "converted_value": parsed.isoformat(),
                "value_type": TYPE_LABELS["datetime"],
                "problems": [],
            }
        return {
            "converted_value": text,
            "value_type": "str",
            "problems": [
                _problem(PROBLEM_DATE,
                         f"日期 {text!r} 不匹配适用格式 {', '.join(formats)}"),
            ],
        }

    return {"converted_value": text, "value_type": TYPE_LABELS["string"], "problems": []}


# ---------------------------------------------------------------------------
# 预置的候选规则与本地网页快照
# ---------------------------------------------------------------------------

def _seed_candidates() -> dict[str, dict[str, Any]]:
    return {
        "P": {
            "candidate_id": "P",
            "name": "候选P · 访问量+ISO日期",
            "target_site": "news.example.com",
            "applicable_page_groups": ["新闻区"],
            "field_mapping": {
                "访问量": "//span[@class='views']",
                "发布日期": "//span[@class='date']",
            },
            "field_types": {"访问量": "int", "发布日期": "date"},
            "date_formats": ["%Y-%m-%d"],
            "clean_regex": "",
            "extract_order": 1,
            "description": "能从新闻快照取出访问量；日期只认 ISO，斜杠日期会报格式异常。",
            "registered_at": _now(),
        },
        "Q": {
            "candidate_id": "Q",
            "name": "候选Q · 斜杠日期",
            "target_site": "news.example.com",
            "applicable_page_groups": ["新闻区", "栏目区"],
            "field_mapping": {
                "访问量": "//div[@class='pv']",
                "发布日期": "//span[@class='date']",
                "标题": "//h1[@class='title']",
            },
            "field_types": {"访问量": "int", "发布日期": "date", "标题": "string"},
            "date_formats": ["%Y/%m/%d"],
            "clean_regex": "",
            "extract_order": 2,
            "description": "日期格式恰好适配新闻页的斜杠日期；新闻页缺少它声明的访问量节点。",
            "registered_at": _now(),
        },
    }


def _seed_snapshots() -> dict[str, dict[str, Any]]:
    def snapshot(page_id: str, page_group: str, title: str, nodes: dict[str, str]) -> dict[str, Any]:
        return {
            "page_id": page_id,
            "page_group": page_group,
            "site": "news.example.com",
            "title": title,
            "url": f"local://snapshots/{page_id}",
            "nodes": nodes,
            "revision": 1,
            "revisions": [{
                "revision": 1,
                "nodes": copy.deepcopy(nodes),
                "changed_at": _now(),
                "operator": "采集快照",
                "note": "初始本地快照",
            }],
            "updated_at": _now(),
        }

    return {
        "news-001": snapshot(
            "news-001", "新闻区", "新闻详情页",
            {
                "//span[@class='views']": "7",
                "//span[@class='date']": "2026/11/21",
                "//h1[@class='title']": "本地新闻快照样例",
            },
        ),
        "column-bad": snapshot(
            "column-bad", "栏目区", "栏目坏日期页",
            {
                "//div[@class='pv']": "12",
                "//span[@class='date']": "2026-13-45",
                "//h1[@class='title']": "栏目坏日期样例",
            },
        ),
        "column-good": snapshot(
            "column-good", "栏目区", "栏目正常日期页",
            {
                "//div[@class='pv']": "9",
                "//span[@class='date']": "2026/11/22",
                "//h1[@class='title']": "栏目正常日期样例",
            },
        ),
    }


# ---------------------------------------------------------------------------
# 登记表：全部数据只在内存里，和正式 store 完全隔离
# ---------------------------------------------------------------------------

class TrialRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.candidates: dict[str, dict[str, Any]] = _seed_candidates()
            self.snapshots: dict[str, dict[str, Any]] = _seed_snapshots()
            self.runs: dict[str, dict[str, Any]] = {}
            self.specimens: dict[str, list[dict[str, Any]]] = {}
            self.acceptances: dict[str, dict[str, Any]] = {}
            self._run_seq = 0
            self._acceptance_seq = 0
            self._formal_baseline: dict[str, str] | None = None

    @property
    def lock(self) -> threading.RLock:
        return self._lock


trial_registry = TrialRegistry()


# ---------------------------------------------------------------------------
# 正式数据只读保护
# ---------------------------------------------------------------------------

FORMAL_MODULES = ("content_extract", "field_clean", "batch_store")


def formal_fingerprint() -> dict[str, str]:
    """给正式数据算指纹，用来证明试跑 / 验收没有偷偷写库。"""
    result: dict[str, str] = {}
    for module in FORMAL_MODULES:
        payload = json.dumps(store.rows(module), ensure_ascii=False, sort_keys=True, default=str)
        result[module] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result


def capture_formal_baseline() -> dict[str, str]:
    if trial_registry._formal_baseline is None:
        trial_registry._formal_baseline = formal_fingerprint()
    return trial_registry._formal_baseline


def formal_unchanged() -> tuple[bool, dict[str, Any]]:
    baseline = capture_formal_baseline()
    current = formal_fingerprint()
    changed = [name for name in baseline if baseline[name] != current[name]]
    return not changed, {"changed_modules": changed, "baseline": baseline, "current": current}


def formal_clean_rules() -> list[dict[str, Any]]:
    """正式清洗当前真正生效的规则（只读展示，不参与试跑改写）。"""
    return [
        {
            "规则编号": row.get("规则编号"),
            "目标字段": row.get("目标字段"),
            "清洗类型": row.get("清洗类型"),
            "处理表达式": row.get("处理表达式"),
            "规则状态": row.get("status"),
        }
        for row in store.rows("field_clean")
        if row.get("status") == "生效中"
    ]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _next_id(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:04d}"


def list_candidates() -> list[dict[str, Any]]:
    with trial_registry.lock:
        return [copy.deepcopy(c) for c in trial_registry.candidates.values()]


def get_candidate(candidate_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        candidate = trial_registry.candidates.get(candidate_id)
        return copy.deepcopy(candidate) if candidate else None


def register_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """登记一条候选规则；允许测试或运营临时追加候选。"""
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raise ValueError("缺少候选规则编号 candidate_id")
    field_mapping = payload.get("field_mapping") or {}
    if not isinstance(field_mapping, dict) or not field_mapping:
        raise ValueError("候选规则至少需要一个字段映射 field_mapping")
    field_types = payload.get("field_types") or {}
    candidate = {
        "candidate_id": candidate_id,
        "name": str(payload.get("name") or f"候选{candidate_id}"),
        "target_site": str(payload.get("target_site") or ""),
        "applicable_page_groups": list(payload.get("applicable_page_groups") or []),
        "field_mapping": {str(k): str(v) for k, v in field_mapping.items()},
        "field_types": {str(k): str(v) for k, v in field_types.items()},
        "date_formats": list(payload.get("date_formats") or []),
        "clean_regex": str(payload.get("clean_regex") or ""),
        "extract_order": int(payload.get("extract_order") or 0),
        "description": str(payload.get("description") or ""),
        "registered_at": _now(),
    }
    with trial_registry.lock:
        trial_registry.candidates[candidate_id] = candidate
    return copy.deepcopy(candidate)


def list_snapshots() -> list[dict[str, Any]]:
    with trial_registry.lock:
        return [copy.deepcopy(s) for s in trial_registry.snapshots.values()]


def get_snapshot(page_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        snapshot = trial_registry.snapshots.get(page_id)
        return copy.deepcopy(snapshot) if snapshot else None


def upsert_snapshot(page_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """登记或更正一条本地网页采集快照，更正会留下修订记录。"""
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("缺少页面编号 page_id")
    with trial_registry.lock:
        snapshot = trial_registry.snapshots.get(page_id)
        if snapshot is None:
            snapshot = {
                "page_id": page_id,
                "page_group": str(payload.get("page_group") or "未分组"),
                "site": str(payload.get("site") or ""),
                "title": str(payload.get("title") or page_id),
                "url": str(payload.get("url") or f"local://snapshots/{page_id}"),
                "nodes": {},
                "revision": 0,
                "revisions": [],
                "updated_at": _now(),
            }
            trial_registry.snapshots[page_id] = snapshot
        if payload.get("page_group"):
            snapshot["page_group"] = str(payload["page_group"])
        if payload.get("title"):
            snapshot["title"] = str(payload["title"])
        if payload.get("site"):
            snapshot["site"] = str(payload["site"])
        nodes = payload.get("nodes")
        if nodes is not None:
            if not isinstance(nodes, dict):
                raise ValueError("nodes 必须是 XPath → 文本值的映射")
            snapshot["nodes"] = {str(k): ("" if v is None else str(v)) for k, v in nodes.items()}
        snapshot["revision"] = int(snapshot.get("revision", 0)) + 1
        snapshot["updated_at"] = _now()
        snapshot.setdefault("revisions", []).append({
            "revision": snapshot["revision"],
            "nodes": copy.deepcopy(snapshot["nodes"]),
            "changed_at": snapshot["updated_at"],
            "operator": str(payload.get("operator") or "值班人员"),
            "note": str(payload.get("note") or "更正本地快照"),
        })
        return copy.deepcopy(snapshot)


# ---------------------------------------------------------------------------
# 单页抽取与试样生成
# ---------------------------------------------------------------------------

def extract_from_snapshot(candidate: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """按候选规则的字段映射，从一条本地快照里逐字段取值并做类型转换。

    返回每字段的 ``raw_value`` / ``converted_value`` / ``value_type`` /
    ``problems``，以及未被规则用到、但快照里存在的字段（供回查页面原貌）。
    """
    nodes: dict[str, str] = snapshot.get("nodes") or {}
    types = candidate.get("field_types") or {}
    date_formats = candidate.get("date_formats") or None

    fields: dict[str, dict[str, Any]] = {}
    for field_name, xpath in (candidate.get("field_mapping") or {}).items():
        if xpath in nodes:
            raw_value: Any = nodes[xpath]
            present = True
        else:
            raw_value = None
            present = False
        conversion = convert_value(raw_value, types.get(field_name, "string"), date_formats)
        problems = list(conversion["problems"])
        fields[field_name] = {
            "field": field_name,
            "xpath": xpath,
            "present": present,
            "raw_value": raw_value,
            "converted_value": conversion["converted_value"],
            "value_type": conversion["value_type"],
            "problems": problems,
        }

    used_xpaths = set((candidate.get("field_mapping") or {}).values())
    unused = {
        xpath: value for xpath, value in nodes.items() if xpath not in used_xpaths
    }
    return {"fields": fields, "unused_nodes": unused}


def _page_group_applicable(candidate: dict[str, Any], page_group: str) -> bool:
    applicable = candidate.get("applicable_page_groups") or []
    if not applicable:
        return True
    return page_group in applicable


def build_specimen(run: dict[str, Any], candidate: dict[str, Any],
                   snapshot: dict[str, Any], index: int) -> dict[str, Any]:
    """为一条候选规则 × 一个网页生成一份可回查的试样。"""
    extraction = extract_from_snapshot(candidate, snapshot)
    applicable = _page_group_applicable(candidate, snapshot.get("page_group", ""))
    problems = [
        problem
        for field in extraction["fields"].values()
        for problem in field["problems"]
    ]
    if not applicable:
        problems.append(_problem(
            PROBLEM_PAGE_GROUP,
            f"{snapshot.get('page_group')} 不在适用页面组 {candidate.get('applicable_page_groups')} 内",
        ))
    if not candidate.get("applicable_page_groups"):
        problems.append(_problem(PROBLEM_PAGE_GROUP, "候选规则没有声明适用页面组"))

    trace_key = json.dumps(
        {
            "run": run["run_id"],
            "candidate": candidate["candidate_id"],
            "page": snapshot["page_id"],
            "revision": snapshot["revision"],
            "index": index,
        },
        ensure_ascii=False, sort_keys=True,
    )
    trace_id = "TR-" + hashlib.sha256(trace_key.encode("utf-8")).hexdigest()[:12]

    return {
        "specimen_id": f"{run['run_id']}-S{index:03d}",
        "trace_id": trace_id,
        "run_id": run["run_id"],
        "sample_group": run["sample_group"],
        "candidate_id": candidate["candidate_id"],
        "candidate_name": candidate.get("name", ""),
        "candidate_snapshot": copy.deepcopy(candidate),
        "page_id": snapshot["page_id"],
        "page_group": snapshot.get("page_group", ""),
        "page_title": snapshot.get("title", ""),
        "page_url": snapshot.get("url", ""),
        "page_revision": snapshot["revision"],
        "page_snapshot": {
            "nodes": copy.deepcopy(snapshot.get("nodes") or {}),
            "revision": snapshot["revision"],
        },
        "applicable": applicable,
        "fields": extraction["fields"],
        "unused_nodes": extraction["unused_nodes"],
        "problems": problems,
        "problem_count": len(problems),
        "field_summary": {
            name: {
                "raw_value": field["raw_value"],
                "converted_value": field["converted_value"],
                "value_type": field["value_type"],
            }
            for name, field in extraction["fields"].items()
        },
        "state": STATE_UNCONFIRMED,
        "signed_at": None,
        "signed_by": None,
        "superseded_by": None,
        "created_at": _now(),
    }



# ---------------------------------------------------------------------------
# 批量试跑
# ---------------------------------------------------------------------------

def _normalise_ids(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _rule_digest(snapshot: dict[str, Any]) -> str:
    keys = ("candidate_id", "field_mapping", "field_types", "date_formats",
            "clean_regex", "applicable_page_groups")
    payload = json.dumps({key: snapshot.get(key) for key in keys},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _combos(candidate_ids: list[str], page_ids: list[str]) -> list[tuple[str, str]]:
    return [(cid, pid) for cid in candidate_ids for pid in page_ids]


def _validate_combos(candidate_ids: list[str], page_ids: list[str]) -> None:
    if not candidate_ids:
        raise ValueError("至少选择一条候选规则")
    if not page_ids:
        raise ValueError("至少选择一个网页采集快照")
    for cid in candidate_ids:
        if cid not in trial_registry.candidates:
            raise ValueError(f"候选规则 {cid} 不存在")
    for pid in page_ids:
        if pid not in trial_registry.snapshots:
            raise ValueError(f"网页采集快照 {pid} 不存在")


def _run_view(run: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(run)


def start_run(sample_group: str, candidate_ids: list[str], page_ids: list[str],
              *, interrupted: bool = False, operator: str = "运营") -> dict[str, Any]:
    """一次提交同组样本，对多条候选规则逐页批量试跑。

    ``interrupted=True`` 只跑前半段，模拟批量试跑中断，可用于验证重试逻辑。
    """
    sample_group = str(sample_group or "").strip()
    if not sample_group:
        raise ValueError("缺少同组样本标识 sample_group")
    candidate_ids = _normalise_ids(candidate_ids)
    page_ids = _normalise_ids(page_ids)

    with trial_registry.lock:
        _validate_combos(candidate_ids, page_ids)
        trial_registry._run_seq += 1
        run_id = _next_id("RUN", trial_registry._run_seq)
        combos = _combos(candidate_ids, page_ids)
        limit = len(combos)
        if interrupted:
            limit = max(1, (len(combos) + 1) // 2)
        run = {
            "run_id": run_id,
            "sample_group": sample_group,
            "candidate_ids": candidate_ids,
            "page_ids": page_ids,
            "operator": operator,
            "created_at": _now(),
            "updated_at": _now(),
            "retry_of": None,
            "retry_count": 0,
            "interrupted": interrupted,
            "expected_specimens": len(combos),
            "produced_specimens": limit,
            "complete": limit == len(combos),
            "status": "已中断" if limit < len(combos) else "已完成",
            "pending_combos": combos[limit:],
            "formal_writes": False,
        }
        specimens = [
            build_specimen(run, trial_registry.candidates[cid],
                           trial_registry.snapshots[pid], index + 1)
            for index, (cid, pid) in enumerate(combos[:limit])
        ]
        for specimen in specimens:
            specimen["rule_digest"] = _rule_digest(specimen["candidate_snapshot"])
        trial_registry.runs[run_id] = run
        trial_registry.specimens[run_id] = specimens
        capture_formal_baseline()
        return {"run": _run_view(run), "report": _report_for_run(run)}


def list_runs() -> list[dict[str, Any]]:
    with trial_registry.lock:
        return [_run_view(run) for run in trial_registry.runs.values()]


def get_run(run_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        return _run_view(run) if run else None


def get_specimen(run_id: str, specimen_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        for specimen in trial_registry.specimens.get(run_id, []):
            if specimen["specimen_id"] == specimen_id or specimen["trace_id"] == specimen_id:
                return copy.deepcopy(specimen)
        return None


# ---------------------------------------------------------------------------
# 试样报告
# ---------------------------------------------------------------------------

def _specimen_summary(specimen: dict[str, Any], *, include_fields: bool = True) -> dict[str, Any]:
    summary = {
        "specimen_id": specimen["specimen_id"],
        "trace_id": specimen["trace_id"],
        "run_id": specimen["run_id"],
        "sample_group": specimen["sample_group"],
        "candidate_id": specimen["candidate_id"],
        "candidate_name": specimen.get("candidate_name", ""),
        "page_id": specimen["page_id"],
        "page_group": specimen["page_group"],
        "page_title": specimen["page_title"],
        "page_revision": specimen["page_revision"],
        "applicable": specimen["applicable"],
        "state": specimen["state"],
        "signed_at": specimen.get("signed_at"),
        "signed_by": specimen.get("signed_by"),
        "problem_count": specimen["problem_count"],
        "problems": copy.deepcopy(specimen["problems"]),
        "rule_digest": specimen.get("rule_digest"),
        "history_count": len(specimen.get("history") or []),
    }
    if include_fields:
        summary["fields"] = copy.deepcopy(specimen["fields"])
    return summary


def _report_for_run(run: dict[str, Any]) -> dict[str, Any]:
    specimens = trial_registry.specimens.get(run["run_id"], [])
    pages: dict[str, dict[str, Any]] = {}
    for specimen in specimens:
        page = pages.setdefault(specimen["page_id"], {
            "page_id": specimen["page_id"],
            "page_group": specimen["page_group"],
            "title": specimen["page_title"],
            "url": specimen["page_url"],
            "revision": specimen["page_revision"],
            "specimens": [],
        })
        page["specimens"].append(_specimen_summary(specimen))
    problem_totals: dict[str, int] = {}
    for specimen in specimens:
        for problem in specimen["problems"]:
            problem_totals[problem["code"]] = problem_totals.get(problem["code"], 0) + 1
    return {
        "run": _run_view(run),
        "page_count": len(pages),
        "specimen_count": len(specimens),
        "pages": sorted(pages.values(), key=lambda item: (item["page_group"], item["page_id"])),
        "problem_totals": problem_totals,
        "state_totals": {
            STATE_UNCONFIRMED: sum(1 for s in specimens if s["state"] == STATE_UNCONFIRMED),
            STATE_SIGNED: sum(1 for s in specimens if s["state"] == STATE_SIGNED),
        },
    }


def run_report(run_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        if run is None:
            return None
        report = _report_for_run(run)
        report["formal_writes"] = False
        return report


def retry_run(run_id: str, *, operator: str = "运营") -> dict[str, Any]:
    """批量试跑中断后重试：只替换本次未确认的试样，已签来源原样保留。"""
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        if run is None:
            raise ValueError(f"试跑批次 {run_id} 不存在")
        existing = {
            (s["candidate_id"], s["page_id"]): s
            for s in trial_registry.specimens.get(run_id, [])
        }
        run["retry_count"] = int(run.get("retry_count", 0)) + 1
        run["retry_of"] = run_id if not run.get("retry_of") else run["retry_of"]
        run["updated_at"] = _now()
        run["operator"] = operator
        replaced: list[dict[str, Any]] = []
        kept_signed: list[dict[str, Any]] = []
        rebuilt: list[dict[str, Any]] = []
        combos = _combos(run["candidate_ids"], run["page_ids"])
        for index, (cid, pid) in enumerate(combos):
            previous = existing.get((cid, pid))
            if previous is not None and previous["state"] == STATE_SIGNED:
                rebuilt.append(previous)
                kept_signed.append({
                    "specimen_id": previous["specimen_id"],
                    "trace_id": previous["trace_id"],
                    "candidate_id": cid,
                    "page_id": pid,
                    "page_revision": previous["page_revision"],
                    "signed_at": previous.get("signed_at"),
                })
                continue
            specimen = build_specimen(run, trial_registry.candidates[cid],
                                      trial_registry.snapshots[pid], index + 1)
            specimen["rule_digest"] = _rule_digest(specimen["candidate_snapshot"])
            if previous is not None:
                history = copy.deepcopy(previous.get("history") or [])
                history.append({
                    "specimen": copy.deepcopy(previous),
                    "superseded_at": _now(),
                    "reason": ("批量试跑重试，页面版本已变化，替换未确认试样"
                               if previous["page_revision"] != specimen["page_revision"]
                               else "批量试跑重试，页面未变，同版本重新生成试样"),
                    "superseded_by": specimen["trace_id"],
                })
                specimen["history"] = history
                if previous["trace_id"] != specimen["trace_id"]:
                    specimen["replaces"] = previous["trace_id"]
                replaced.append({
                    "specimen_id": previous["specimen_id"],
                    "old_trace_id": previous["trace_id"],
                    "new_trace_id": specimen["trace_id"],
                    "candidate_id": cid,
                    "page_id": pid,
                    "old_revision": previous["page_revision"],
                    "new_revision": specimen["page_revision"],
                    "problem_count": specimen["problem_count"],
                    "kind": ("页面已更正，重新生成"
                             if previous["page_revision"] != specimen["page_revision"]
                             else "页面未变，同版本重新生成"),
                })
            rebuilt.append(specimen)
        trial_registry.specimens[run_id] = rebuilt
        run["interrupted"] = False
        run["status"] = "已完成"
        run["complete"] = True
        run["produced_specimens"] = len(rebuilt)
        run["pending_combos"] = []
        return {
            "run": _run_view(run),
            "report": _report_for_run(run),
            "replaced": replaced,
            "kept_signed": kept_signed,
            "formal_writes": False,
        }


# ---------------------------------------------------------------------------
# 试跑结果 → 字段清洗清单
# ---------------------------------------------------------------------------

def cleaning_manifest(run_id: str) -> dict[str, Any] | None:
    """把一次试跑同步成字段清洗清单，供清洗端逐页核对。"""
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        if run is None:
            return None
        specimens = trial_registry.specimens.get(run_id, [])
        items: list[dict[str, Any]] = []
        fields_seen: dict[str, set[str]] = {}
        for specimen in specimens:
            for field_name, field in specimen["fields"].items():
                fields_seen.setdefault(field_name, set()).add(field["value_type"])
                items.append({
                    "specimen_id": specimen["specimen_id"],
                    "trace_id": specimen["trace_id"],
                    "candidate_id": specimen["candidate_id"],
                    "page_id": specimen["page_id"],
                    "page_group": specimen["page_group"],
                    "page_revision": specimen["page_revision"],
                    "field": field_name,
                    "xpath": field["xpath"],
                    "raw_value": field["raw_value"],
                    "converted_value": field["converted_value"],
                    "value_type": field["value_type"],
                    "problems": copy.deepcopy(field["problems"]),
                    "state": specimen["state"],
                })
        return {
            "run_id": run_id,
            "sample_group": run["sample_group"],
            "source": "试样报告",
            "synced_at": _now(),
            "items": items,
            "field_type_summary": {
                name: sorted(types) for name, types in sorted(fields_seen.items())
            },
            "formal_clean_rules": formal_clean_rules(),
            "formal_writes": False,
        }


# ---------------------------------------------------------------------------
# 字段清洗验收
# ---------------------------------------------------------------------------

def _acceptance_view(record: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(record)


def _sign_specimen(specimen: dict[str, Any], operator: str, note: str,
                   acceptance_id: str) -> None:
    specimen["state"] = STATE_SIGNED
    specimen["signed_at"] = _now()
    specimen["signed_by"] = operator
    specimen["history"] = list(specimen.get("history") or []) + [{
        "action": "签为通过",
        "acceptance_id": acceptance_id,
        "operator": operator,
        "note": note,
        "at": specimen["signed_at"],
        "trace_id": specimen["trace_id"],
        "rule_digest": specimen.get("rule_digest"),
        "page_revision": specimen["page_revision"],
    }]


def _evaluate_specimen(specimen: dict[str, Any]) -> dict[str, Any]:
    """判断一份试样是否够资格签为通过，并说明理由。"""
    reasons: list[str] = []
    if not specimen.get("trace_id"):
        reasons.append("缺少试样回查号")
    if not specimen.get("candidate_snapshot"):
        reasons.append("无法回查候选规则快照")
    if not specimen.get("page_group"):
        reasons.append("试样没有适用页面组")
    if not specimen.get("page_snapshot"):
        reasons.append("试样没有页面原值快照")
    if specimen.get("problems"):
        codes = sorted({p["code"] for p in specimen["problems"]})
        reasons.append("存在转换问题：" + "、".join(codes))
    return {"trace_id": specimen.get("trace_id"), "reasons": reasons,
            "eligible": not reasons}


def confirm_acceptance(run_id: str, *, operator: str = "维护者",
                       note: str = "", specimen_ids: list[str] | None = None) -> dict[str, Any]:
    """清洗端发起一次验收确认。

    * 只对可回查（候选规则 + 适用页面组 + 页面原值齐全）且无转换问题的试样
      签为通过；
    * 其余试样保持未确认并写清暂缓原因；
    * 每次确认都留一份不可变快照，供后续回查当时到底领到了什么。
    """
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        if run is None:
            raise ValueError(f"试跑批次 {run_id} 不存在")
        specimens = trial_registry.specimens.get(run_id, [])
        wanted = set(_normalise_ids(specimen_ids)) if specimen_ids else None

        trial_registry._acceptance_seq += 1
        acceptance_id = _next_id("ACC", trial_registry._acceptance_seq)

        eligible_tests: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        signed_now: list[dict[str, Any]] = []
        already_signed: list[dict[str, Any]] = []
        received: list[dict[str, Any]] = []

        for specimen in specimens:
            if wanted is not None and specimen["trace_id"] not in wanted \
                    and specimen["specimen_id"] not in wanted:
                continue
            received.append({
                "specimen_id": specimen["specimen_id"],
                "trace_id": specimen["trace_id"],
                "candidate_id": specimen["candidate_id"],
                "page_id": specimen["page_id"],
                "page_group": specimen["page_group"],
                "page_revision": specimen["page_revision"],
                "state": specimen["state"],
            })
            if specimen["state"] == STATE_SIGNED:
                already_signed.append({
                    "specimen_id": specimen["specimen_id"],
                    "trace_id": specimen["trace_id"],
                    "signed_at": specimen.get("signed_at"),
                })
                continue
            evaluation = _evaluate_specimen(specimen)
            if evaluation["eligible"]:
                _sign_specimen(specimen, operator, note, acceptance_id)
                signed_now.append({
                    "specimen_id": specimen["specimen_id"],
                    "trace_id": specimen["trace_id"],
                    "candidate_id": specimen["candidate_id"],
                    "page_id": specimen["page_id"],
                    "page_group": specimen["page_group"],
                })
                eligible_tests.append({
                    "test_id": f"T-{specimen['trace_id']}",
                    "trace_id": specimen["trace_id"],
                    "candidate_id": specimen["candidate_id"],
                    "page_id": specimen["page_id"],
                    "page_group": specimen["page_group"],
                    "page_revision": specimen["page_revision"],
                    "publish_ready": True,
                })
            else:
                deferred.append({
                    "specimen_id": specimen["specimen_id"],
                    "trace_id": specimen["trace_id"],
                    "candidate_id": specimen["candidate_id"],
                    "page_id": specimen["page_id"],
                    "page_group": specimen["page_group"],
                    "page_revision": specimen["page_revision"],
                    "reasons": evaluation["reasons"],
                    "problems": copy.deepcopy(specimen["problems"]),
                })

        page_groups = _page_group_eligibility(specimens)
        conflicts = _detect_conflicts(run, specimens, wanted)

        all_candidates = {s["candidate_id"] for s in specimens}
        all_pages = {s["page_id"] for s in specimens}
        produced_combos = {(s["candidate_id"], s["page_id"]) for s in specimens}
        not_covered = [
            {"candidate_id": cid, "page_id": pid, "reason": "本批试样未覆盖该候选×页面组合"}
            for cid in run["candidate_ids"] for pid in run["page_ids"]
            if (cid, pid) not in produced_combos
        ]
        pending_retry = [
            {"candidate_id": cid, "page_id": pid, "reason": "批量试跑中断，尚未生成试样"}
            for cid, pid in run.get("pending_combos", [])
        ]

        record = {
            "acceptance_id": acceptance_id,
            "run_id": run_id,
            "sample_group": run["sample_group"],
            "operator": operator,
            "note": note,
            "confirmed_at": _now(),
            "run_state_at_confirm": {
                "status": run.get("status"),
                "complete": run.get("complete"),
                "produced_specimens": run.get("produced_specimens"),
                "expected_specimens": run.get("expected_specimens"),
            },
            "received": received,
            "received_count": len(received),
            "eligible_tests": eligible_tests,
            "deferred": deferred,
            "signed_now": signed_now,
            "already_signed": already_signed,
            "page_groups": page_groups,
            "conflicts": conflicts,
            "not_covered": not_covered,
            "pending_retry": pending_retry,
            "candidate_scope": sorted(all_candidates),
            "page_scope": sorted(all_pages),
            "formal_writes": False,
        }
        trial_registry.acceptances[acceptance_id] = record
        return _acceptance_view(record)


def _page_group_eligibility(specimens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """汇总每个页面组是否已经拿到“可以交给发布验收”的资格。

    只统计对该页面组适用的试样；候选规则明确不适用的页面组不会被拿去凑数，
    也不会因为别的候选在该组有问题而被判为“可交发布验收”。
    """
    groups: dict[str, dict[str, Any]] = {}
    for specimen in specimens:
        group = groups.setdefault(specimen["page_group"] or "未分组", {
            "page_group": specimen["page_group"] or "未分组",
            "specimen_count": 0,
            "out_of_scope_count": 0,
            "signed_count": 0,
            "deferred_count": 0,
            "pages": {},
        })
        page = group["pages"].setdefault(specimen["page_id"], {
            "page_id": specimen["page_id"],
            "signed": 0,
            "deferred": 0,
            "out_of_scope": 0,
            "reasons": [],
        })
        if not specimen["applicable"]:
            group["out_of_scope_count"] += 1
            page["out_of_scope"] += 1
            continue
        group["specimen_count"] += 1
        if specimen["state"] == STATE_SIGNED:
            group["signed_count"] += 1
            page["signed"] += 1
        else:
            group["deferred_count"] += 1
            page["deferred"] += 1
            evaluation = _evaluate_specimen(specimen)
            for reason in evaluation["reasons"]:
                if reason not in page["reasons"]:
                    page["reasons"].append(reason)
    for group in groups.values():
        group["pages"] = sorted(group["pages"].values(), key=lambda item: item["page_id"])
        group["eligible"] = group["deferred_count"] == 0 and group["specimen_count"] > 0
        group["status"] = "可交发布验收" if group["eligible"] else "仍需补测"
    return sorted(groups.values(), key=lambda item: item["page_group"])


def _detect_conflicts(run: dict[str, Any], specimens: list[dict[str, Any]],
                      wanted: set[str] | None) -> list[dict[str, Any]]:
    """找出同批不同口径试样在“可确认范围”上的冲突。

    典型场景：某区域当时还没补齐样例，另一条候选只认了一部分页面，或验收
    只挑了本批的部分试样确认，导致不同口径对同一页面的结论对不上。
    """
    conflicts: list[dict[str, Any]] = []
    by_page: dict[str, list[dict[str, Any]]] = {}
    for specimen in specimens:
        if specimen["applicable"]:
            by_page.setdefault(specimen["page_id"], []).append(specimen)

    seen_pairs: set[tuple[str, str]] = set()
    for page_id, items in sorted(by_page.items()):
        verdicts = {s["state"] for s in items}
        if len(verdicts) > 1:
            seen_pairs.add((page_id, items[0]["page_group"]))
            conflicts.append({
                "code": "同页签署口径不一致",
                "page_id": page_id,
                "page_group": items[0]["page_group"],
                "detail": "同一页面在本批试样里既有已签也有未确认：" + "、".join(
                    f"{s['candidate_id']}@{s['page_revision']}={s['state']}" for s in items),
                "specimens": [s["trace_id"] for s in items],
            })

    applicable_groups = sorted({
        group
        for specimen in specimens
        for group in (specimen["candidate_snapshot"].get("applicable_page_groups") or [])
    })
    covered_groups = sorted({s["page_group"] for s in specimens})
    for group in applicable_groups:
        if group not in covered_groups:
            conflicts.append({
                "code": "适用页面组缺样例",
                "page_group": group,
                "detail": f"候选规则声明适用「{group}」，但本批样本里没有该区域的网页快照",
                "specimens": [],
            })

    for candidate_id in run["candidate_ids"]:
        candidate_specimens = [s for s in specimens if s["candidate_id"] == candidate_id]
        if not candidate_specimens:
            conflicts.append({
                "code": "候选规则无试样",
                "candidate_id": candidate_id,
                "detail": f"候选 {candidate_id} 在本批样本里没有领到任何试样，不能签通过",
                "specimens": [],
            })
            continue
        out_of_scope = [s for s in candidate_specimens if not s["applicable"]]
        if out_of_scope:
            conflicts.append({
                "code": "候选规则越出适用页面组",
                "candidate_id": candidate_id,
                "detail": "候选 " + candidate_id + " 被拿去跑了不适用的页面组：" + "、".join(
                    sorted({s["page_group"] for s in out_of_scope})),
                "specimens": [s["trace_id"] for s in out_of_scope],
            })

    if wanted is not None:
        skipped = [s for s in specimens
                   if s["trace_id"] not in wanted and s["specimen_id"] not in wanted]
        if skipped:
            conflicts.append({
                "code": "验收口径只覆盖部分试样",
                "detail": f"本批共 {len(specimens)} 份试样，本次确认只领到 {len(specimens) - len(skipped)} 份，"
                          f"其余 {len(skipped)} 份本批次未纳入本次可确认范围",
                "specimens": [s["trace_id"] for s in skipped],
            })

    for group in _page_group_eligibility(specimens):
        if group["eligible"]:
            continue
        group_specimens = [s for s in specimens
                           if s["applicable"] and (s["page_group"] or "未分组") == group["page_group"]]
        signed = [s for s in group_specimens if s["state"] == STATE_SIGNED]
        unsigned = [s for s in group_specimens if s["state"] != STATE_SIGNED]
        if signed and unsigned:
            conflicts.append({
                "code": "页面组内部签署冲突",
                "page_group": group["page_group"],
                "detail": f"「{group['page_group']}」已有 {len(signed)} 份试样签为通过，"
                          f"但还有 {len(unsigned)} 份未确认，整组仍不可交发布验收",
                "specimens": [s["trace_id"] for s in group_specimens],
            })
    return conflicts

def list_acceptances(run_id: str | None = None) -> list[dict[str, Any]]:
    with trial_registry.lock:
        records = list(trial_registry.acceptances.values())
        if run_id:
            records = [r for r in records if r["run_id"] == run_id]
        return [_acceptance_view(r) for r in records]


def get_acceptance(acceptance_id: str) -> dict[str, Any] | None:
    with trial_registry.lock:
        record = trial_registry.acceptances.get(acceptance_id)
        return _acceptance_view(record) if record else None


def run_history(run_id: str) -> dict[str, Any] | None:
    """回查一次试跑里的全部变更：重试替换、签署历史、暂缓原因。"""
    with trial_registry.lock:
        run = trial_registry.runs.get(run_id)
        if run is None:
            return None
        specimens = trial_registry.specimens.get(run_id, [])
        return {
            "run": _run_view(run),
            "specimens": [_specimen_summary(s, include_fields=False) for s in specimens],
            "history": [
                {
                    "specimen_id": specimen["specimen_id"],
                    "trace_id": specimen["trace_id"],
                    "replaces": specimen.get("replaces"),
                    "history": copy.deepcopy(specimen.get("history") or []),
                }
                for specimen in specimens
                if specimen.get("history") or specimen.get("replaces")
            ],
            "acceptances": list_acceptances(run_id),
        }


# ---------------------------------------------------------------------------
# 一键示例：把题面描述的四步走成可复现的剧本
# ---------------------------------------------------------------------------

def scenario() -> dict[str, Any]:
    """在隔离的登记表上重放完整示例，返回每一步的可回查结果。"""
    with trial_registry.lock:
        trial_registry.reset()
        capture_formal_baseline()
        result: dict[str, Any] = {}

        result["candidates"] = [get_candidate("P"), get_candidate("Q")]
        result["snapshots"] = list_snapshots()

        first = start_run(
            "样本组-NEWS-COLUMN",
            ["P", "Q"],
            ["news-001", "column-bad", "column-good"],
            operator="运营值班",
        )
        result["first_run"] = first

        manifest = cleaning_manifest(first["run"]["run_id"])
        result["cleaning_manifest"] = manifest

        before_fix = confirm_acceptance(
            first["run"]["run_id"], operator="维护者",
            note="坏日期更正前先提交一次确认",
        )
        result["acceptance_before_fix"] = before_fix

        bad_live = trial_registry.snapshots["column-bad"]["nodes"]
        fixed = upsert_snapshot("column-bad", {
            "nodes": {
                **bad_live,
                "//span[@class='date']": "2026/11/22",
                "//h1[@class='title']": "栏目坏日期样例（已更正）",
            },
            "operator": "值班人员",
            "note": "坏日期更正为 2026/11/22",
        })
        result["fixed_snapshot"] = fixed

        retried = retry_run(first["run"]["run_id"], operator="运营值班")
        result["retry"] = retried

        after_fix = confirm_acceptance(
            first["run"]["run_id"], operator="维护者",
            note="更正后重新确认",
        )
        result["acceptance_after_fix"] = after_fix

        interrupted = start_run(
            "样本组-COLUMN-中断",
            ["Q"],
            ["column-good", "column-bad"],
            interrupted=True,
            operator="运营值班",
        )
        interrupted_acceptance = confirm_acceptance(
            interrupted["run"]["run_id"], operator="维护者",
            note="中断批次里先确认已产出的试样",
        )
        interrupted_retry = retry_run(interrupted["run"]["run_id"], operator="运营值班")
        result["interrupted_run"] = {
            "run": interrupted["run"],
            "report": interrupted["report"],
            "acceptance": interrupted_acceptance,
            "retry": interrupted_retry,
        }

        result["history"] = run_history(first["run"]["run_id"])
        result["acceptances"] = list_acceptances(first["run"]["run_id"])
        result["formal_clean_rules"] = formal_clean_rules()
        unchanged, detail = formal_unchanged()
        result["formal_unchanged"] = unchanged
        result["formal_detail"] = detail
        return result
