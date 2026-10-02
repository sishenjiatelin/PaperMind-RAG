# M4｜服务运营页面与演示流程

> 页面明确标示“本地演示”和“合成工单数据”。工单、设备、站点及 SLA 来自 M0 生成规则；厂商 PDF 与项目自编记录便签分别显示来源和版本。

## 启动

在仓库根目录运行：

```bash
python scripts/start_dashboard.py
```

默认仅监听 `localhost:8501`。打开左侧导航的 **Service Operations**。新页面的代码、数据读取和测试均在 `service_operations/`；原 Dashboard 入口只增加一项导航注册。

页面首次可没有工单库和手册索引。在「数据维护」中点击 **导入 M0 合成样本**，随后点击 **下载并建立演示手册索引**。后者从 M0 选定的官方地址下载两份 PDF，按固定 SHA-256 校验，并索引厂商手册与项目自编的两版 PDF；已存在的相同版本会直接复用。下载需要网络，约 49 MB；本地已下载并核验后无需重复下载。

## 给演示者的最短路径

完整逐步讲述稿见 [`../examples/m4/demo_script.md`](../examples/m4/demo_script.md)。界面上的关键动作如下：

1. **看数据状态。** 顶部显示活动快照、激活时间、18 台设备、670 条工单、4 条 SLA 策略和质量问题数。所有规模只代表固定合成样本。
2. **看异常趋势并追溯。** 「指标与趋势」保留默认的 2026-06-01 至 2026-09-01 区间，选 `TAZ_PRO` 与 `NOZZLE_WIPE`，趋势指标选「30 天重复故障率」。点击柱形或使用下方月份选择框。选 2026-08 后可看到该月 `8/9`，展开构成分母的工单；`metric_outcome` 标出是否重复，`prior_work_order_id` 指向前序工单。
3. **提联合问题。** 「联合提问」保留默认问题与条件，点击查询。固定样本的本期结果为 `25/33=75.76%`，等长前期为 `11/21=52.38%`，变化 `+23.38` 个百分点。文档证据含当前自编便签 v2 的 PDF 第 1 页，以及厂商 TAZ Pro 手册的 PDF 第 83 页；两者性质明确区分。
4. **验证拒收与回滚。** 「数据维护」点击 **导入故意错误批次**，在批次报告查看 6 条质量问题。顶部质量问题数增加，但活动快照与 670 条工单保持不变。
5. **验证历史版本。** 「联合提问」切换为手册问题，把查询时点改成 `2026-06-01T00:00:00+08:00`，问题写 `NOZZLE_WIPE 服务记录流程`，可看到当时适用的 v1；改回 2026-09-28 为 v2。

## 页面结构与数据来源

| 页面区块 | 来源和边界 |
| --- | --- |
| 数据状态 | [`dashboard_data.py`](../dashboard_data.py) 对活动快照、表计数和批次做只读 SQLite 查询；缺库时显示初始化入口 |
| 三项指标卡 | 调用 [`query_service.py`](../query_service.py) 与 M1 预定义 SQL；展示分子、分母、口径、快照、等长前期变化 |
| 月度趋势 | 同一 M1 指标函数逐月计算，支持总体／型号／站点／故障分类分组；日期和分组由页面选择，SQL 不在 UI 拼接 |
| 工单明细 | 由所选趋势点的 `eligible_work_order_ids` 参数化回查；展示设备、站点、时间、优先级、SLA 策略与指标结果，分页每次最多 200 条 |
| 联合提问 | 调用 M3 服务；展示回答、警告、SQL 证据、同一快照的前期比较、文档版本／来源／物理 PDF 页码／原文摘录 |
| 数据维护 | 只调用 M1 批次导入和 M2 哈希核验索引服务；列出成功／拒收报告、错误原因、手册生效日期和 Chroma/BM25 索引健康状态 |

普通页面不提供删除快照、删除手册或手工改库操作。`as_of` 既控制手册版本，也控制 SLA 到期判断；历史 `as_of` **不重建当时的数据库写入历史**。指标图的区间为半开区间 `[start, end)`，日期按 `Asia/Shanghai` 解释。空分母显示“无样本”，不会画成 0%。

## 备份与恢复

SQLite 使用 WAL；运行中备份应使用 SQLite backup API。以下命令在仓库根目录创建一致的数据库副本：

```bash
mkdir -p service_operations/runtime/backup
python - <<'PY'
import sqlite3
from pathlib import Path
source = sqlite3.connect('service_operations/runtime/service_operations.db')
target = sqlite3.connect('service_operations/runtime/backup/service_operations.db')
source.backup(target)
source.close()
target.close()
PY
```

恢复时先停止 Dashboard 和导入进程，保留当前库的副本，再把备份库复制回 `service_operations/runtime/service_operations.db`。手册索引可在停止应用后整体复制 `service_operations/runtime/manual_index/`；也可按 M2 文档重新执行 `fetch` 与 `index`。所有 `runtime/` 内容都被 git 忽略。

## 验证范围

```bash
.venv/bin/python -m pytest -q service_operations/tests
.venv/bin/python -m ruff check service_operations
```

M4 测试覆盖空库、活动快照、月度分组、工单追溯、错误批次与活动快照隔离、手册双索引状态、图表点选映射、页面无浏览器渲染，以及从页面按钮完成“导入→联合提问→拒收报告”。未进行跨浏览器视觉验收、性能压测或真实用户任务测试；这些属于 M5 作品验收。
