"""M3 service: route defined tasks and combine SQL values with versioned PDF evidence."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from service_operations.contracts import ServiceOpsRequest, parse_instant
from service_operations.manuals.search import ManualIndex
from service_operations.metrics.query import query_metric

LOGGER = logging.getLogger(__name__)
FEATURE_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = FEATURE_ROOT / "runtime/service_operations.db"
DEFAULT_MANUAL_INDEX = FEATURE_ROOT / "runtime/manual_index"

METRIC_PATTERNS = {
    "mttr_hours": re.compile(r"MTTR|平均修复(?:时长|时间)|mean time to repair", re.I),
    "sla_attainment": re.compile(r"SLA|按时完成率|达成率|service level", re.I),
    "repeat_fault_rate_30d": re.compile(r"重复故障率|重复率|复发率|repeat fault rate", re.I),
}
DOCUMENT_PATTERN = re.compile(
    r"手册|排障|排查|检查|如何处理|怎么处理|处置|处理步骤|处理方法|维修|文档|版本|记录流程|服务记录|"
    r"manual|troubleshoot|repair|procedure|service record|which version",
    re.I,
)
COMPARISON_PATTERN = re.compile(r"上升|下降|变化|环比|前一|相比|趋势|较上|compare|change|increase|decrease|previous", re.I)
FAULT_SEARCH_TERMS = {"NOZZLE_WIPE": "PROBE FAIL CLEAN NOZZLE"}
GENERIC_QUERY_TERMS = {"taz", "pro", "workhorse", "manual", "user", "service", "the", "for", "and", "how"}


def _add_warning(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _choose_metric(request: ServiceOpsRequest) -> tuple[str | None, bool]:
    if request.metric_id:
        return request.metric_id, False
    matches = [metric_id for metric_id, pattern in METRIC_PATTERNS.items()
               if pattern.search(request.question)]
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def _choose_mode(request: ServiceOpsRequest, metric_id: str | None, ambiguous: bool) -> str:
    if request.mode != "auto":
        return request.mode
    if ambiguous:
        return "auto"
    has_document = bool(DOCUMENT_PATTERN.search(request.question))
    if metric_id and has_document:
        return "combined"
    if metric_id:
        return "metric"
    if has_document:
        return "document"
    return "auto"


def _format_metric(metric: dict[str, Any]) -> str:
    if metric["value"] is None:
        return f"{metric['metric_id']}：该区间没有符合口径的工单，无法计算。"
    value = metric["value"]
    if metric["unit"] == "ratio":
        amount = f"{value * 100:.2f}%"
    else:
        amount = f"{value:.2f} 小时"
    return (f"{metric['metric_id']}：{amount}（分子 {metric['numerator']}，"
            f"分母 {metric['denominator']}；快照 {metric['snapshot_id']}）。")


def _comparison(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    if current["value"] is None or previous["value"] is None:
        delta = None
    elif current["unit"] == "ratio":
        delta = round((current["value"] - previous["value"]) * 100, 4)
    else:
        delta = round(current["value"] - previous["value"], 6)
    direction = None if delta is None else ("increase" if delta > 0 else "decrease" if delta < 0 else "flat")
    return {
        "previous": previous,
        "delta": delta,
        "delta_unit": "percentage_points" if current["unit"] == "ratio" else "hours",
        "direction": direction,
    }


def _format_comparison(value: dict[str, Any]) -> str:
    previous = value["previous"]
    if value["delta"] is None:
        return "前期或本期缺少符合口径的工单，无法计算变化。"
    sign = "+" if value["delta"] > 0 else ""
    unit = "个百分点" if value["delta_unit"] == "percentage_points" else "小时"
    if previous["unit"] == "ratio":
        prior_value = f"{previous['value'] * 100:.2f}%"
    else:
        prior_value = f"{previous['value']:.2f} 小时"
    return f"等长前期为 {prior_value}，变化 {sign}{value['delta']:.2f} {unit}。"


def _citation(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "logical_doc_id": hit["logical_doc_id"],
        "model": hit["model"],
        "version": hit["version"],
        "title": hit["title"],
        "source": hit["source"],
        "source_hash": hit["source_hash"],
        "license": hit["license"],
        "page": hit["page"],
        "page_or_section": f"PDF p.{hit['page']}",
        "effective_from": hit["effective_from"],
        "effective_to": hit["effective_to"],
        "chunk_id": hit["chunk_id"],
        "excerpt": hit["text"],
        "source_kind": "demo_note" if hit["logical_doc_id"].startswith("demo_") else "manufacturer_manual",
    }


def _format_documents(citations: list[dict[str, Any]], question: str) -> str:
    if not citations:
        return "没有找到可核对的当前适用文档证据。"
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for citation in citations:
        unique.setdefault((citation["logical_doc_id"], citation["version"]), citation)
    references = []
    for item in unique.values():
        title = item["title"]
        if not title.lower().endswith(item["version"].lower()):
            title += " " + item["version"]
        references.append(f"{title}（PDF 第 {item['page']} 页）")
    preferred = citations[0]
    if re.search(r"记录|流程|材料|record|profile", question, re.I):
        preferred = next((item for item in citations if item["source_kind"] == "demo_note"), preferred)
    excerpt = preferred["excerpt"]
    if preferred["source_kind"] == "demo_note" and "For internal" in excerpt:
        excerpt = excerpt[excerpt.index("For internal"):]
        excerpt = excerpt.split("For troubleshooting", 1)[0]
    elif "Resolution:" in excerpt:
        excerpt = excerpt.split("Resolution:", 1)[1]
    excerpt = " ".join(excerpt.split())[:280].rstrip(".。 ")
    return ("适用资料：" + "；".join(references) + "。"
            + f"引自 {preferred['title']} PDF 第 {preferred['page']} 页：{excerpt}"
            + "。演示便签不是厂商维修指引。")


class ServiceOpsQueryService:
    def __init__(self, db_path: str | Path = DEFAULT_DB,
                 manual_index: ManualIndex | None = None,
                 manual_index_dir: str | Path = DEFAULT_MANUAL_INDEX):
        self.db_path = Path(db_path)
        self.manual_index = manual_index
        self.manual_index_dir = Path(manual_index_dir)

    def _manuals(self) -> ManualIndex:
        if self.manual_index is None:
            self.manual_index = ManualIndex(self.manual_index_dir)
        return self.manual_index

    def query(self, request: ServiceOpsRequest | Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, ServiceOpsRequest):
            request = ServiceOpsRequest.from_dict(request)
        metric_id, ambiguous = _choose_metric(request)
        mode = _choose_mode(request, metric_id, ambiguous)
        at = parse_instant(request.as_of).isoformat() if request.as_of else datetime.now(timezone.utc).isoformat()
        response: dict[str, Any] = {
            "mode": mode, "answer": "", "metrics": [], "comparison": None,
            "citations": [], "data_as_of": at, "warnings": [],
        }
        warnings: list[str] = response["warnings"]
        if ambiguous and not request.metric_id and mode != "document":
            _add_warning(warnings, "AMBIGUOUS_METRIC")
            response["answer"] = "问题涉及多个已定义指标，请明确选择 metric_id。"
            response["available_metric_ids"] = sorted(METRIC_PATTERNS)
            return response
        if mode == "auto":
            _add_warning(warnings, "UNRESOLVED_INTENT")
            response["answer"] = "无法确定任务类型，请选择 document、metric 或 combined，并提供必要筛选条件。"
            response["available_modes"] = ["document", "metric", "combined"]
            return response

        metric_text = ""
        document_text = ""
        if mode in {"metric", "combined"}:
            if metric_id is None:
                _add_warning(warnings, "METRIC_ID_REQUIRED")
            elif request.period is None:
                _add_warning(warnings, "MISSING_PERIOD")
            elif mode == "combined" and request.model is None:
                _add_warning(warnings, "MISSING_MODEL")
            else:
                start_dt, end_dt = request.period.bounds()
                if end_dt > parse_instant(at):
                    _add_warning(warnings, "PERIOD_AFTER_AS_OF")
                else:
                    try:
                        current = query_metric(
                            self.db_path, metric_id, request.period.start, request.period.end,
                            as_of=at, snapshot_id=request.snapshot_id, model=request.model,
                            site=request.site, priority=request.priority, fault_code=request.fault_code,
                        )
                        response["metrics"].append(current)
                        if current["warning"]:
                            _add_warning(warnings, "NO_ELIGIBLE_WORK_ORDERS")
                        metric_text = _format_metric(current)
                    except ValueError as exc:
                        if "No active snapshot" in str(exc):
                            _add_warning(warnings, "NO_ACTIVE_SNAPSHOT")
                        elif "Unknown imported snapshot" in str(exc):
                            _add_warning(warnings, "SNAPSHOT_NOT_FOUND")
                        else:
                            LOGGER.exception("Metric query failed")
                            _add_warning(warnings, "METRIC_UNAVAILABLE")
                    except (sqlite3.Error, OSError, RuntimeError):
                        LOGGER.exception("Metric query failed")
                        _add_warning(warnings, "METRIC_UNAVAILABLE")
                    if response["metrics"]:
                        compare = request.compare_previous
                        if compare is None:
                            compare = bool(COMPARISON_PATTERN.search(request.question))
                        if compare:
                            try:
                                duration = end_dt - start_dt
                                previous = query_metric(
                                    self.db_path, metric_id,
                                    (start_dt - duration).isoformat(), start_dt.isoformat(),
                                    as_of=at, snapshot_id=current["snapshot_id"], model=request.model,
                                    site=request.site, priority=request.priority, fault_code=request.fault_code,
                                )
                                response["comparison"] = _comparison(current, previous)
                                if previous["warning"]:
                                    _add_warning(warnings, "NO_PREVIOUS_ELIGIBLE_WORK_ORDERS")
                                metric_text += " " + _format_comparison(response["comparison"])
                            except (ValueError, sqlite3.Error, OSError, RuntimeError):
                                LOGGER.exception("Previous-period metric query failed")
                                _add_warning(warnings, "COMPARISON_UNAVAILABLE")

        if mode in {"document", "combined"}:
            if request.model is None:
                _add_warning(warnings, "MISSING_MODEL")
            else:
                try:
                    manuals = self._manuals()
                    eligible = manuals.registry.select(request.model, at, request.fault_code)
                    if not eligible:
                        _add_warning(warnings, "NO_APPLICABLE_MANUAL")
                    else:
                        search_text = request.question
                        if request.fault_code:
                            search_text += " " + request.fault_code
                            search_text += " " + FAULT_SEARCH_TERMS.get(request.fault_code, "")
                        hits = manuals.search(search_text, request.model, as_of=at,
                                              fault_code=request.fault_code,
                                              top_k=max(10, request.top_k * 3))
                        record_task = bool(re.search(r"记录|流程|service record|profile", request.question, re.I))
                        substantive_terms = set(manuals.encoder._tokenize(search_text)) - GENERIC_QUERY_TERMS
                        candidates = []
                        for hit in hits:
                            if hit["model"] != request.model or hit["matched_terms"] == 0:
                                continue
                            if not substantive_terms.intersection(manuals.encoder._tokenize(hit["text"])):
                                continue
                            if hit["logical_doc_id"].startswith("demo_") and not record_task:
                                continue
                            effective_from = parse_instant(hit["effective_from"])
                            effective_to = parse_instant(hit["effective_to"]) if hit["effective_to"] else None
                            if effective_from > parse_instant(at) or (effective_to and parse_instant(at) >= effective_to):
                                _add_warning(warnings, "DOCUMENT_CONTEXT_MISMATCH")
                                continue
                            candidates.append(_citation(hit))
                        if record_task:
                            candidates.sort(key=lambda item: item["source_kind"] != "demo_note")
                        seen_versions = set()
                        for citation in candidates:
                            identity = (citation["logical_doc_id"], citation["version"])
                            if identity in seen_versions:
                                continue
                            seen_versions.add(identity)
                            response["citations"].append(citation)
                            if len(response["citations"]) >= request.top_k:
                                break
                        if not response["citations"]:
                            _add_warning(warnings, "NO_RELEVANT_MANUAL_EVIDENCE")
                    document_text = _format_documents(response["citations"], request.question)
                except Exception:
                    LOGGER.exception("Manual query failed")
                    _add_warning(warnings, "DOCUMENT_SEARCH_UNAVAILABLE")

        if mode == "combined":
            if not response["metrics"]:
                _add_warning(warnings, "METRIC_EVIDENCE_MISSING")
            if not response["citations"]:
                _add_warning(warnings, "DOCUMENT_EVIDENCE_MISSING")
        response["answer"] = " ".join(part for part in (metric_text, document_text) if part)
        if not response["answer"]:
            response["answer"] = "现有数据或文档不足以回答；请查看 warnings 并补充条件。"
        return response
