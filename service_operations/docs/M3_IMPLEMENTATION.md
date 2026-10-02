# M3｜受控联合查询与证据合成

> 本阶段以 M0 的**合成工单**和 M2 的公开手册／项目自编便签演示。输出不是厂商售后建议，也不是企业真实运营指标。

## 复现

先按 [M1](M1_IMPLEMENTATION.md) 导入工单快照，并按 [M2](M2_IMPLEMENTATION.md) 下载和索引手册。随后在仓库根目录运行：

```bash
python -m service_operations.cli ask \
  --question '近三个月 TAZ Pro 的 NOZZLE_WIPE 重复故障率是否上升？应按哪个版本的材料记录流程处理？' \
  --mode auto --model TAZ_PRO --fault-code NOZZLE_WIPE \
  --start 2026-06-01 --end 2026-09-01 \
  --as-of 2026-09-28T00:00:00+08:00

python -m service_operations.cli ask \
  --question 'TAZ Pro 的 PROBE FAIL CLEAN NOZZLE 应如何排查？' \
  --mode document --model TAZ_PRO --fault-code NOZZLE_WIPE \
  --as-of 2026-09-28T00:00:00+08:00

python -m service_operations.cli ask \
  --question '2026 年第二季度的平均修复时长是多少？' \
  --mode metric --metric-id mttr_hours --model TAZ_PRO \
  --start 2026-04-01 --end 2026-07-01 \
  --as-of 2026-09-28T00:00:00+08:00
```

若要复现历史手册版本，把文档命令的 `--as-of` 改为 `2026-06-01T00:00:00+08:00`，应引用 v1；2026-07-01 起引用 v2。默认业务库与手册索引均位于 `service_operations/runtime/`；`--db` 是主命令参数，放在 `ask` **之前**，`--index-dir` 是 `ask` 参数。

## 契约和执行顺序

`ServiceOpsRequest` 的字段为 `question`、`mode`、`metric_id`、`model`、`site`、`priority`、`fault_code`、`period` (`start`/`end`)、`as_of`、`snapshot_id`、`compare_previous`、`top_k`。代码入口为 [`../contracts.py`](../contracts.py) 和 [`../query_service.py`](../query_service.py)，CLI 入口为 [`../cli.py`](../cli.py)。

1. 验证字段和枚举；`YYYY-MM-DD` 解释为上海时间午夜，完整时间必须带时区。拒绝未知字段、未知指标、非法型号与无效区间。
2. `auto` 仅识别三项**已定义**指标和文档线索；多个指标或无法判断时要求明确 `metric_id`／`mode`。不从自由文本猜测站点、日期或生成任意 SQL。
3. 指标侧调用 M1 的参数化 SQL，并附带口径版本、分子／分母、入选工单 ID、筛选条件及快照。出现“上升／下降／变化／环比”等问题，或显式 `--compare-previous` 时，比较相邻**等长**时间区间；前期使用同一快照和 `as_of`。比率变化用百分点，MTTR 变化用小时。零分母返回 `null` 和警告。
4. 文档侧先按型号、故障分类和 `as_of` 从注册表选有效版本，再搜索各版本独立的 Chroma/BM25 索引。`NOZZLE_WIPE` 显式映射到手册术语 `PROBE FAIL CLEAN NOZZLE`；没有定义的分类不臆造厂商错误码。返回逻辑文档 ID、版本、来源、许可、物理 PDF 页码、生效区间和原文摘录。PDF 目录页在候选排序中降权；每个逻辑文档版本只返回一条优先证据。技术排障问题不引用项目自编的记录便签，记录流程问题则优先返回当前便签。
5. 合成答复只格式化 SQL 数值和有出处的短摘录。手册无证据时不补写处理步骤，SQL 失败时不估算指标。`combined` 缺任一路会在 `warnings` 中明确说明，另一侧的有效结果仍可查看。

响应包含 `mode`、`answer`、`metrics`、`comparison`、`citations`、`data_as_of`、`warnings`。`as_of` 同时用于 SLA 到期判断和手册适用版本；`snapshot_id` 表示工单数据快照。**历史 `as_of` 不等同于数据库的历史可见状态**；当前系统不重建某日真实的工单录入历史。为了避免查询区间延伸到 `as_of` 之后，M3 对这类指标请求返回 `PERIOD_AFTER_AS_OF`。

## 可核对的演示结果

固定 M0 样本下，`TAZ_PRO`、`NOZZLE_WIPE` 的 2026-06-01 至 2026-09-01 重复故障率为 `25/33 = 75.7576%`；等长前期为 `11/21 = 52.3810%`，变化约 `+23.38` 个百分点。这个上升是**合成规则刻意注入的模式**，不能解释成真实设备故障趋势。同一问题在 2026-09-28 查询到演示记录便签 v2；TAZ Pro 厂商手册对应故障说明可定位到 PDF 物理第 83 页。

## 警告语义与边界

| 警告 | 触发条件 |
| --- | --- |
| `AMBIGUOUS_METRIC` / `UNRESOLVED_INTENT` | 无法安全决定指标或任务类型 |
| `METRIC_ID_REQUIRED` / `MISSING_PERIOD` / `MISSING_MODEL` | 请求缺少必需的结构化条件 |
| `NO_ACTIVE_SNAPSHOT` / `SNAPSHOT_NOT_FOUND` / `METRIC_UNAVAILABLE` | 指标数据不可用；不估算数字 |
| `PERIOD_AFTER_AS_OF` | 指标区间包含查询时点之后的时间 |
| `NO_ELIGIBLE_WORK_ORDERS` / `NO_PREVIOUS_ELIGIBLE_WORK_ORDERS` | 本期／前期没有符合口径的工单 |
| `COMPARISON_UNAVAILABLE` | 本期值可用，但前期查询失败 |
| `NO_APPLICABLE_MANUAL` / `NO_RELEVANT_MANUAL_EVIDENCE` | 无生效版本或无足够文本匹配 |
| `DOCUMENT_SEARCH_UNAVAILABLE` / `DOCUMENT_CONTEXT_MISMATCH` | 索引失败或证据的型号／时间与请求不符 |
| `METRIC_EVIDENCE_MISSING` / `DOCUMENT_EVIDENCE_MISSING` | 联合问题缺少其中一路证据 |

当前回答为**确定性引用模板**，未接入生成式模型；文档检索的向量分支仍是 M2 的词项哈希基线。实际检索与答复质量需在 M5 的独立任务集上评估。M4 页面已使用同一服务层，不在页面内复制业务 SQL 或版本判断。

## 验证

```bash
.venv/bin/python -m pytest -q service_operations/tests
.venv/bin/python -m ruff check service_operations
```

M3 测试覆盖联合问题与前期比较、历史／当前版本、零分母、任一路失败、歧义意图、时间约束、非法筛选、比较失败后保留本期值，以及技术手册与演示记录便签的来源选择。
