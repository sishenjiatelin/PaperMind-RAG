# Service Operations｜设备售后服务运营扩展

本目录集中维护数据与企业数字化方向的项目扩展。M0 已固定业务任务、手册来源和合成规则；M1 已实现可复现的 SQLite 快照导入、质量报告及三项 SQL 指标；M2 已实现手册版本注册、双索引与按时间检索；M3 已实现指标与手册的受控联合查询；M4 已实现服务运营页面与演示流程；M5 已交付任务集、自动评估、复现说明与作品案例，文档证据的独立人工复核仍待完成。

## 目录

| 路径 | 内容 |
| --- | --- |
| [`DEV_SPEC.md`](DEV_SPEC.md) | 目标、架构、里程碑和验收门槛 |
| [`docs/`](docs/) | M0–M5 说明、架构图、中英文案例、官方手册来源及自编版本便签 |
| [`config/m0.json`](config/m0.json) | 合成数据规则和随机种子 |
| [`examples/m0/`](examples/m0/) | 可复现的合成 CSV、错误注入样本、指标参考答案 |
| [`examples/sql/`](examples/sql/) | 三条可单独运行的 SQL 示例 |
| [`examples/m5/tasks.jsonl`](examples/m5/tasks.jsonl) | 固定的 100 例任务、80/20 分层划分、自动期望值、逐例结果及待填人工复核表 |
| [`evaluate.py`](evaluate.py)、[`evaluation/`](evaluation/) | 独立 CSV 参考计算、BM25 对照与逐例失败报告 |
| [`warehouse/`](warehouse/)、[`metrics/`](metrics/) | 快照导入、数据校验、SQLite schema 和指标查询 |
| [`manuals/`](manuals/) | 手册注册表、PDF 索引与版本查询 CLI |
| [`contracts.py`](contracts.py)、[`query_service.py`](query_service.py) | M3 结构化请求、路由和证据合成 |
| [`dashboard.py`](dashboard.py)、[`dashboard_data.py`](dashboard_data.py) | M4 页面与只读数据服务 |
| [`examples/m4/demo_script.md`](examples/m4/demo_script.md) | 非开发者演示讲述稿 |
| [`tests/`](tests/) | 本扩展独立的回归测试 |
| `runtime/` | 本地演示数据库；不纳入版本控制 |

在**仓库根目录**运行：

```bash
python -m service_operations.generate_m0
python -m service_operations.cli import
python -m service_operations.cli status
python -m service_operations.cli metric --id mttr_hours --start 2026-01-01 --end 2026-04-01 --as-of 2026-09-28T00:00:00+08:00 --model TAZ_PRO
python -m service_operations.manuals.cli fetch
python -m service_operations.manuals.cli index
python -m service_operations.manuals.cli search --model TAZ_PRO --fault-code NOZZLE_WIPE --as-of 2026-09-28T00:00:00+08:00 --query "NOZZLE_WIPE printing material Cura profile"
python -m service_operations.cli ask --question "NOZZLE_WIPE 重复故障率是否上升？应按哪个版本记录流程？" --model TAZ_PRO --fault-code NOZZLE_WIPE --start 2026-06-01 --end 2026-09-01 --as-of 2026-09-28T00:00:00+08:00
.venv/bin/python -m service_operations.evaluation.tasks
.venv/bin/python -m service_operations.evaluate --split development
.venv/bin/python -m service_operations.evaluate --split holdout
.venv/bin/pytest -q service_operations/tests
```

启动页面：`python scripts/start_dashboard.py`，进入 **Service Operations**。页面复现步骤见 [M4 说明](docs/M4_IMPLEMENTATION.md)。

详细字段、合成规则和指标参考值见 [M0 说明](docs/M0_FOUNDATION.md)；导入失败演示、报告命令和 SQL 示例见 [M1 说明](docs/M1_IMPLEMENTATION.md)。M2 的来源、版本索引与测试见 [M2 说明](docs/M2_IMPLEMENTATION.md)；M3 的联合查询契约和复现命令见 [M3 说明](docs/M3_IMPLEMENTATION.md)。演示设备、站点、工单和 SLA 均为**合成数据**，不是任何企业的实际服务记录。

## M5 复现与作品材料

在新检出的仓库中先按根目录依赖说明安装 Python 环境，然后按上方顺序生成 CSV、导入快照、下载并索引手册、生成任务与评估。两份官方 PDF 需约 49 MB 下载，固定 SHA-256 校验；自编便签 PDF 已在仓库。评估默认读取 `runtime/`，逐例 JSON 写入 `runtime/evaluation/`。也可用 `--db`、`--index-dir`、`--tasks` 和 `--output-dir` 指向隔离目录。原始 CSV 或手册内容改变时，原评估数字失效，应重建任务并重新留出保留集。

[架构与数据流](docs/ARCHITECTURE.md) · [M5 评估报告](docs/M5_EVALUATION.md) · [中文一页案例](docs/CASE_STUDY_ZH.md) · [English one-page case](docs/CASE_STUDY_EN.md)。固定本地样本自动验收 100/100，其中保留集 20/20；文档原始 Hit@3 为 16/20。全部工单为合成数据，答案为确定性模板，PDF 页码与处理语义仍需独立人工复核。
