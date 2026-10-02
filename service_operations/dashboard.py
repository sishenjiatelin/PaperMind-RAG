"""Streamlit Service Operations page. Business queries live in feature services."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from service_operations.contracts import QueryPeriod, ServiceOpsRequest
from service_operations.dashboard_data import (
    DEFAULT_DB,
    DEFAULT_INDEX,
    ServiceOpsDashboardData,
)
from service_operations.metrics.query import METRIC_IDS
from service_operations.query_service import ServiceOpsQueryService
from service_operations.warehouse.importer import (
    FAULT_CODES,
    MODELS,
    PRIORITIES,
    SITES,
    import_snapshot,
)

FEATURE_ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = FEATURE_ROOT / "examples/m0"
METRIC_LABELS = {
    "mttr_hours": "平均修复时长",
    "sla_attainment": "SLA 达成率",
    "repeat_fault_rate_30d": "30 天重复故障率",
}
GROUP_LABELS = {"all": "总体", "model": "型号", "site": "站点", "fault_code": "故障分类"}


def _option(label: str, values: list[str], *, key: str, index: int = 0) -> str | None:
    selected = st.selectbox(label, ["全部", *values], index=index, key=key)
    return None if selected == "全部" else selected


def _format_value(metric: dict[str, Any]) -> str:
    if metric["value"] is None:
        return "无样本"
    if metric["unit"] == "ratio":
        return f"{metric['value'] * 100:.1f}%"
    return f"{metric['value']:.1f} 小时"


def _format_delta(comparison: dict[str, Any] | None) -> str | None:
    if not comparison or comparison["delta"] is None:
        return None
    unit = "百分点" if comparison["delta_unit"] == "percentage_points" else "小时"
    return f"{comparison['delta']:+.1f} {unit} 较等长前期"


def _chart_row_from_selection(event: Any, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    selection = getattr(event, "selection", None)
    if not selection:
        return None
    points = selection.get("pick", [])
    if not isinstance(points, list) or not points:
        return None
    chosen = points[0]
    if not isinstance(chosen, dict):
        return None
    return next((row for row in rows if row["period_start"] == chosen.get("period_start")
                 and row["group"] == chosen.get("group")), None)


def _show_status(data: ServiceOpsDashboardData, status: dict[str, Any]) -> None:
    st.title("设备售后服务运营")
    st.warning("本地演示 · 工单、设备、站点和 SLA 均为合成数据；厂商手册为公开资料，记录流程便签为项目自编。")
    if not status["ready"]:
        st.info("尚无可用工单快照。可在「数据维护」中导入 M0 合成样本；文档查询仍可单独使用。")
        return
    cols = st.columns(5)
    cols[0].metric("活动快照", status["snapshot_id"][-8:])
    cols[1].metric("设备", status["row_counts"]["assets"])
    cols[2].metric("工单", status["row_counts"]["work_orders"])
    cols[3].metric("SLA 策略", status["row_counts"]["sla_policies"])
    cols[4].metric("质量问题记录", status["quality_issue_count"])
    st.caption(f"快照激活：{status['activated_at']} · schema {status['schema_version']} · "
               f"完整快照 ID：{status['snapshot_id']}")


def _show_metrics(data: ServiceOpsDashboardData, status: dict[str, Any],
                  app: ServiceOpsQueryService, default_as_of: str) -> None:
    st.subheader("指标总览与趋势")
    if not status["ready"]:
        st.info("先在「数据维护」导入合成工单快照。")
        return
    defaults = status["default_period"] or {"start": "2026-06-01", "end": "2026-09-01"}
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            model = _option("型号", sorted(MODELS), key="ops_metric_model")
        with c2:
            site = _option("站点", sorted(SITES), key="ops_metric_site")
        with c3:
            fault_code = _option("故障分类", sorted(FAULT_CODES), key="ops_metric_fault")
        c4, c5, c6 = st.columns(3)
        with c4:
            start = st.date_input("开始日期（含）", value=date.fromisoformat(defaults["start"]), key="ops_metric_start")
        with c5:
            end = st.date_input("结束日期（不含）", value=date.fromisoformat(defaults["end"]), key="ops_metric_end")
        with c6:
            as_of = st.text_input("查询时点（带时区）", value=default_as_of, key="ops_metric_asof")
    try:
        period = QueryPeriod(start.isoformat(), end.isoformat())
        period.bounds()
        request_base = {"model": model, "site": site, "fault_code": fault_code,
                        "period": period, "as_of": as_of, "snapshot_id": status["snapshot_id"]}
        overview = {}
        for metric_id in METRIC_LABELS:
            result = app.query(ServiceOpsRequest(
                question=METRIC_LABELS[metric_id], mode="metric", metric_id=metric_id,
                compare_previous=True, **request_base,
            ))
            overview[metric_id] = result
        cols = st.columns(3)
        for column, (metric_id, label) in zip(cols, METRIC_LABELS.items()):
            result = overview[metric_id]
            metric = result["metrics"][0] if result["metrics"] else None
            with column:
                if metric:
                    st.metric(label, _format_value(metric), _format_delta(result["comparison"]),
                              delta_color="inverse" if metric_id != "sla_attainment" else "normal")
                    st.caption(f"分子 {metric['numerator']} · 分母 {metric['denominator']} · 口径 {metric['definition_version']}")
                else:
                    st.metric(label, "不可用")
                if result["warnings"]:
                    st.caption("警告：" + ", ".join(result["warnings"]))

        trend_metric = st.selectbox("趋势指标", list(METRIC_LABELS),
                                    format_func=lambda item: METRIC_LABELS[item], key="ops_trend_metric")
        group_by = st.selectbox("分组", list(GROUP_LABELS),
                                format_func=lambda item: GROUP_LABELS[item], key="ops_trend_group")
        rows = data.trend(trend_metric, period, as_of=as_of,
                          snapshot_id=status["snapshot_id"], group_by=group_by,
                          model=model, site=site, fault_code=fault_code)
        chart_data = [{key: row[key] for key in ("period_start", "period_label", "group",
                                               "display_value", "numerator", "denominator")}
                      for row in rows]
        unit = "%" if trend_metric != "mttr_hours" else "小时"
        spec = {
            "params": [{"name": "pick", "select": {"type": "point", "fields": ["period_start", "group"]}}],
            "mark": {"type": "bar", "tooltip": True},
            "encoding": {
                "x": {"field": "period_label", "type": "ordinal", "title": "月份"},
                "xOffset": {"field": "group", "type": "nominal"},
                "y": {"field": "display_value", "type": "quantitative", "title": unit},
                "color": {"field": "group", "type": "nominal", "title": GROUP_LABELS[group_by]},
                "opacity": {"condition": {"param": "pick", "value": 1}, "value": 0.55},
                "tooltip": [
                    {"field": "period_label", "title": "月份"},
                    {"field": "group", "title": "分组"},
                    {"field": "display_value", "title": "指标值", "format": ".2f"},
                    {"field": "numerator", "title": "分子"},
                    {"field": "denominator", "title": "分母"},
                ],
            },
        }
        st.caption("点击柱形选择月份与分组，展开构成该指标的工单；也可使用下方选择框。")
        chart_event = st.vega_lite_chart(chart_data, spec, key=f"ops_chart_{trend_metric}_{group_by}",
                                         on_select="rerun", selection_mode="pick", height=310)
        selected = _chart_row_from_selection(chart_event, rows)
        selected_index = st.selectbox(
            "查看月份／分组工单", range(len(rows)), index=len(rows) - 1,
            format_func=lambda index: f"{rows[index]['period_label']} · {rows[index]['group']}",
            key=f"ops_trend_pick_{trend_metric}_{group_by}",
        )
        if selected is None:
            selected = rows[selected_index]
        metric = selected["metric"]
        st.markdown(f"**{selected['period_label']} · {selected['group']}：{_format_value(metric)}**")
        st.caption(f"分子 {metric['numerator']} · 分母 {metric['denominator']} · "
                   f"口径 {metric['definition_version']} · 快照 {metric['snapshot_id']} · "
                   f"区间 [{metric['period']['start']}, {metric['period']['end']}) · 查询时点 {metric['as_of']}")
        if metric["warning"]:
            st.info(metric["warning"])
        total = len(metric["eligible_work_order_ids"])
        if total:
            page_size = st.selectbox("每页工单", [25, 50, 100, 200], index=1, key="ops_detail_size")
            page = st.number_input("明细页码", min_value=1,
                                   max_value=max(1, math.ceil(total / page_size)),
                                   value=1, step=1, key="ops_detail_page")
            details = data.metric_details(metric, offset=(page - 1) * page_size, limit=page_size)
            st.dataframe(details["rows"], hide_index=True)
            st.caption(f"显示 {details['offset'] + 1}–{details['offset'] + len(details['rows'])} / {details['total']} 条入选工单。"
                       "重复故障明细的 prior_work_order_id 指向前序已解决工单。")
        else:
            st.info("这个月份和分组没有符合该指标口径的工单。")
    except (ValueError, sqlite3.Error, OSError) as exc:
        st.error(f"指标视图不可用：{exc}")


def _show_ask(app: ServiceOpsQueryService, status: dict[str, Any],
              default_as_of: str) -> None:
    st.subheader("联合提问")
    st.caption("问题用于受控路由；型号、时间和指标条件由下方字段指定。数字来自 SQL，文档证据带版本和页码。")
    defaults = status.get("default_period") or {"start": "2026-06-01", "end": "2026-09-01"}
    with st.form("ops_ask_form"):
        question = st.text_area("问题", value="近三个月 TAZ Pro 的 NOZZLE_WIPE 重复故障率是否上升？应按哪个版本的材料记录流程处理？")
        c1, c2, c3 = st.columns(3)
        with c1:
            mode = st.selectbox("任务类型", ["auto", "combined", "metric", "document"],
                                format_func=lambda value: {"auto": "自动识别", "combined": "联合", "metric": "指标", "document": "手册"}[value])
        with c2:
            metric_choice = st.selectbox("指标", ["自动识别", *sorted(METRIC_IDS)],
                                         format_func=lambda value: METRIC_LABELS.get(value, value))
        with c3:
            model_choice = st.selectbox("设备型号", ["未指定", *sorted(MODELS)], index=1)
        c4, c5, c6 = st.columns(3)
        with c4:
            site_choice = st.selectbox("站点", ["全部", *sorted(SITES)])
        with c5:
            fault_choice = st.selectbox("故障分类", ["全部", *sorted(FAULT_CODES)],
                                        index=sorted(FAULT_CODES).index("NOZZLE_WIPE") + 1)
        with c6:
            priority_choice = st.selectbox("优先级", ["全部", *sorted(PRIORITIES)])
        c7, c8, c9 = st.columns(3)
        with c7:
            start = st.date_input("指标开始日期", value=date.fromisoformat(defaults["start"]), key="ops_ask_start")
        with c8:
            end = st.date_input("指标结束日期（不含）", value=date.fromisoformat(defaults["end"]), key="ops_ask_end")
        with c9:
            as_of = st.text_input("查询时点（带时区）", value=default_as_of, key="ops_ask_asof")
        compare_choice = st.selectbox("等长前期比较", ["按问题判断", "是", "否"])
        submitted = st.form_submit_button("查询", type="primary")
    if submitted:
        try:
            request = ServiceOpsRequest(
                question=question, mode=mode,
                metric_id=None if metric_choice == "自动识别" else metric_choice,
                model=None if model_choice == "未指定" else model_choice,
                site=None if site_choice == "全部" else site_choice,
                fault_code=None if fault_choice == "全部" else fault_choice,
                priority=None if priority_choice == "全部" else priority_choice,
                period=QueryPeriod(start.isoformat(), end.isoformat()) if mode != "document" else None,
                as_of=as_of,
                snapshot_id=status.get("snapshot_id"),
                compare_previous={"按问题判断": None, "是": True, "否": False}[compare_choice],
            )
            st.session_state["ops_last_answer"] = app.query(request)
        except ValueError as exc:
            st.error(f"查询条件有误：{exc}")
    result = st.session_state.get("ops_last_answer")
    if not result:
        return
    for warning in result["warnings"]:
        st.warning(warning)
    st.markdown(result["answer"])
    st.caption(f"模式 {result['mode']} · 数据/文档适用时点 {result['data_as_of']}")
    if result["metrics"]:
        st.markdown("#### SQL 指标证据")
        for metric in result["metrics"]:
            st.write(f"**{METRIC_LABELS[metric['metric_id']]}**：{_format_value(metric)} · "
                     f"分子 {metric['numerator']} / 分母 {metric['denominator']}")
            st.caption(f"口径 {metric['definition_version']} · 快照 {metric['snapshot_id']} · "
                       f"区间 [{metric['period']['start']}, {metric['period']['end']}) · "
                       f"筛选 {metric['filters']}")
            with st.expander(f"查看 {len(metric['eligible_work_order_ids'])} 条入选工单 ID"):
                st.write(metric["eligible_work_order_ids"])
    if result["comparison"]:
        comparison = result["comparison"]
        previous = comparison["previous"]
        st.info(f"等长前期：{_format_value(previous)} · {previous['numerator']}/{previous['denominator']} · "
                f"变化 {_format_delta(comparison) or '不可计算'}")
    if result["citations"]:
        st.markdown("#### 手册与流程证据")
        for citation in result["citations"]:
            label = f"{citation['title']} · {citation['version']} · PDF 第 {citation['page']} 页"
            with st.expander(label, expanded=True):
                st.caption(f"适用：{citation['effective_from']} 至 {citation['effective_to'] or '今'} · "
                           f"型号 {citation['model']} · {citation['source_kind']} · 许可 {citation['license']}")
                if citation["source"].startswith("https://"):
                    st.link_button("打开来源 PDF", citation["source"] + f"#page={citation['page']}")
                else:
                    st.code(citation["source"])
                st.write(citation["excerpt"])
                st.caption(f"源文件 SHA-256：{citation['source_hash']}")
    with st.expander("查看完整结构化响应"):
        st.json(result)


def _show_maintenance(data: ServiceOpsDashboardData, db_path: Path,
                      manual_index_dir: Path) -> None:
    st.subheader("数据维护与质量报告")
    st.caption("按钮只导入本仓库公开的合成样本和经哈希核验的手册；没有删除快照或手册的页面操作。")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("导入 M0 合成样本", use_container_width=True):
            try:
                with st.spinner("校验并导入完整快照…"):
                    result = import_snapshot(db_path, SAMPLE_DIR)
                st.session_state["ops_maintenance_result"] = result.to_dict()
                st.rerun()
            except Exception as exc:
                st.error(f"导入失败：{exc}")
    with c2:
        if st.button("导入故意错误批次", use_container_width=True):
            try:
                with st.spinner("校验错误样本…"):
                    result = import_snapshot(
                        db_path, SAMPLE_DIR,
                        extra_work_orders_path=SAMPLE_DIR / "invalid/invalid_work_orders.csv",
                    )
                st.session_state["ops_maintenance_result"] = result.to_dict()
                st.rerun()
            except Exception as exc:
                st.error(f"批次校验失败：{exc}")
    with c3:
        if st.button("下载并建立演示手册索引", use_container_width=True):
            try:
                from service_operations.manuals.cli import fetch, versions
                from service_operations.manuals.search import ManualIndex

                with st.spinner("下载、校验并索引手册 PDF…"):
                    fetch()
                    index = ManualIndex(manual_index_dir)
                    indexed = [index.ingest(doc) for doc in versions()]
                st.session_state["ops_maintenance_result"] = {
                    "status": "manuals_indexed",
                    "versions": [{"logical_doc_id": doc.logical_doc_id, "version": doc.version,
                                  "chunk_count": doc.chunk_count} for doc in indexed],
                }
                st.rerun()
            except Exception as exc:
                st.error(f"手册索引失败：{exc}")
    last_result = st.session_state.get("ops_maintenance_result")
    if last_result:
        if last_result.get("status") == "rejected":
            st.warning("错误批次已拒收；活动快照保持原样。")
        else:
            st.success(f"最近操作：{last_result.get('status')}")
        with st.expander("查看最近一次操作结果", expanded=True):
            st.json(last_result)

    try:
        batches = data.batches()
        if batches:
            st.markdown("#### 导入批次")
            st.dataframe([{key: row[key] for key in ("batch_id", "status", "imported_at", "issue_count", "schema_version")}
                          for row in batches], hide_index=True)
            chosen = st.selectbox("查看批次质量报告", [row["batch_id"] for row in batches],
                                  format_func=lambda batch_id: next(
                                      f"{row['status']} · {row['imported_at']} · {batch_id[-8:]}"
                                      for row in batches if row["batch_id"] == batch_id))
            report = data.batch_report(chosen)
            if report:
                st.caption(f"来源哈希：{report['source_hash']} · 规则分布：{report['rule_counts']}")
                st.dataframe(report["row_counts"])
                if report["issues"]:
                    st.dataframe(report["issues"], hide_index=True)
                else:
                    st.info("这个批次没有质量问题。")
        else:
            st.info("尚无导入批次。")
    except (sqlite3.Error, OSError) as exc:
        st.error(f"批次报告不可用：{exc}")
    try:
        versions = data.manual_versions()
        st.markdown("#### 手册版本与索引状态")
        if versions:
            st.dataframe([{key: row[key] for key in (
                "logical_doc_id", "model", "fault_code", "version", "effective_from",
                "effective_to", "publication_status", "index_ready", "current", "chunk_count",
            )} for row in versions], hide_index=True)
            selected = st.selectbox("查看手册来源", range(len(versions)),
                                    format_func=lambda position: f"{versions[position]['title']} · {versions[position]['version']}")
            item = versions[selected]
            st.caption(f"来源：{item['source']} · 许可：{item['license']} · SHA-256：{item['source_hash']}")
            if not item["index_ready"]:
                st.warning("该版本索引不完整，不能作为已发布的可用证据。")
        else:
            st.info("尚未建立手册索引。")
    except Exception as exc:
        st.error(f"手册状态不可用：{exc}")


def render(db_path: str | Path = DEFAULT_DB,
           manual_index_dir: str | Path = DEFAULT_INDEX) -> None:
    db_path = Path(db_path)
    manual_index_dir = Path(manual_index_dir)
    data = ServiceOpsDashboardData(db_path, manual_index_dir)
    status = data.status()
    _show_status(data, status)
    try:
        default_as_of = json.loads((FEATURE_ROOT / "config/m0.json").read_text(encoding="utf-8"))["as_of"]
    except (OSError, ValueError, KeyError):
        default_as_of = "2026-09-28T00:00:00+08:00"
    app = ServiceOpsQueryService(db_path, manual_index_dir=manual_index_dir)
    metrics_tab, ask_tab, maintenance_tab = st.tabs(["指标与趋势", "联合提问", "数据维护"])
    with metrics_tab:
        _show_metrics(data, status, app, default_as_of)
    with ask_tab:
        _show_ask(app, status, default_as_of)
    with maintenance_tab:
        _show_maintenance(data, db_path, manual_index_dir)


if __name__ == "__main__":
    render()
