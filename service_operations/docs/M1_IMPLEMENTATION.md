# M1｜结构化工单数据与指标

> 适用分支：`feat/service-operations`。本阶段使用 M0 的**合成数据**；不代表真实企业的工单、SLA 或运营结果。

## 运行

在仓库根目录执行，Python 版本至少为 3.10。以下命令只使用 Python 标准库，不需要启动 RAG、向量库或模型服务。默认业务库为 `service_operations/runtime/service_operations.db`（由功能包内的 `.gitignore` 排除）。

```bash
python -m service_operations.cli import
python -m service_operations.cli status
python -m service_operations.cli metric --id mttr_hours --start 2026-01-01 --end 2026-04-01 --as-of 2026-09-28T00:00:00+08:00 --model TAZ_PRO
python -m service_operations.cli metric --id sla_attainment --start 2026-04-01 --end 2026-07-01 --as-of 2026-09-28T00:00:00+08:00 --model TAZ_PRO
python -m service_operations.cli metric --id repeat_fault_rate_30d --start 2026-06-01 --end 2026-09-01 --as-of 2026-09-28T00:00:00+08:00 --model TAZ_PRO --fault-code NOZZLE_WIPE
```

`import` 输出包含 `batch_id`。按同一输入再次执行会返回 `noop`，不会生成新快照。如下命令把 M0 的六条故意错误记录**追加**到有效工单后执行整批校验；命令退出码为 2，报告包含六个规则 ID，当前活动快照保持不变：

```bash
python -m service_operations.cli import --extra-work-orders service_operations/examples/m0/invalid/invalid_work_orders.csv
python -m service_operations.cli report --batch-id <上一步返回的batch_id>
python -m service_operations.cli status
```

可通过全局 `--db /path/to/file.db` 使用独立数据库；`import` 还支持 `--data-dir` 和 `--work-orders`。`metric` 支持已定义的 `model/site/priority/fault_code` 筛选和已导入的 `--snapshot-id`。日期参数 `YYYY-MM-DD` 解释为上海时间当天 00:00；带时间的输入必须包含时区偏移，输出同时给出实际 UTC 边界。`--end` 为开区间。SLA 查询建议显式传 `--as-of`，以保证复现；省略时使用运行时 UTC 当前时刻。

## 数据库与导入语义

`service_operations/warehouse/schema.py` 建立独立的 `ingestion_batches`、`active_snapshot`、`assets`、`sla_policies`、`work_orders` 和 `data_quality_issues`。每个成功批次的 `batch_id` 同时是 `snapshot_id`。旧快照保留，可显式查询；新快照的写入与活动指针切换处于同一事务。数据库约束再次保护主键、外键、状态、时间顺序和策略正小时数。

导入器按 M0 的 `m0-v1` 契约检查 CSV 头、UTF-8、字段数、必填值、枚举、重复 ID、设备关联、带时区时间戳、状态与解决时间关系、SLA 策略生效区间及每张工单的策略匹配。时间入库前统一转换为 UTC，并保存整数微秒时间，用于精确的边界比较；`due_at` 根据工单创建时唯一有效策略和 24×7 自然小时计算。SLA 策略不能重叠或缺失。

`source_hash` 由 schema 版本、固定文件角色和各文件的 SHA-256 组成，用于成功批次幂等。质量错误会保存 `rejected` 批次、逐行问题、规则分布和源文件哈希；不会写入任何该批次业务行。表头错误时该文件所有数据行均计为拒收。批次报告中的 `valid` 是**通过逐行校验的行数**，`imported` 才是实际写入行数；整批拒收时后者为 0。数据库执行中失败会回滚全部新批次写入和活动指针切换。

## 指标与核对

`service_operations/metrics/query.py` 仅执行三条预定义、参数化的指标查询；它不接受模型生成的 SQL。结果包含指标版本、单位、分子、分母、筛选条件、区间、UTC 边界、活动或指定快照、入选工单 ID。SLA 另外列出达标 ID；重复故障另外列出前序工单映射。分母为 0 返回 `null` 与 `NO_ELIGIBLE_RECORDS`。

M0 的 [`reference_cases.json`](../examples/m0/reference_cases.json)由独立 Python 算法生成。M1 测试逐例比较五组分子、分母、结果和工单 ID。其中 TAZ Pro 的 2026 Q2 SLA 为 `70/93`，6–8 月 `NOZZLE_WIPE` 重复率为 `25/33`；这些数值只描述固定合成样本。

三条可单独运行的示例 SQL 位于 [`examples/sql/`](../examples/sql/)；对应运行命令如下，分别展示 JOIN/分组、CTE 和窗口排名：

```bash
python -m service_operations.sql_examples mttr
python -m service_operations.sql_examples sla
python -m service_operations.sql_examples repeat
```

示例按固定样本时间区间查询**当前活动快照**，用于 SQL 作品展示；业务代码以 `query_metric` 的参数化查询为准。

## 已验证的边界

`service_operations/tests/test_m1.py` 覆盖五个参考案例、六类无效记录、重复导入、新旧快照切换、SQLite 注入故障回滚、策略重叠／缺失、CSV 表头及列数异常、参数拒绝、30 天左边界、SLA 到期时刻以及零分母。运行：

```bash
.venv/bin/pytest -q service_operations/tests/test_m1.py
.venv/bin/ruff check service_operations
```

M1 只解决结构化业务数据；手册下载、注册与版本检索属于 M2，联合问答与页面属于后续阶段。
