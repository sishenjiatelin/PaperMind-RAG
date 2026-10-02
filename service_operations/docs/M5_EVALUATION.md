# M5｜任务验收与已知边界

> 本报告是 2026-09-28 在本地合成样本上的自动验收，不是企业真实业务准确率或真实用户测试。评估任务的 PDF 来源、版本和页码标签仍需独立人工复核。

## 固定输入与运行条件

- 任务文件：`examples/m5/tasks.jsonl`，SHA-256 `0a63756e098ff4be354fdcf3ad9d23c833b408e9e711f0d26d90666d9fd2437c`。共 100 例：指标 60、文档 20、混合 12、拒答 8；80 例开发集，20 例保留集。按每类固定编号分层留出，未在开发期用保留集调整排序。
- 数据：18 台虚构设备、670 条合成工单、4 条 SLA 策略。活动快照 `batch-f31adc263e934d55a8b0bd37dc4aaf5c`；来源哈希 `a86e1638f41f3c63a098abf76310de940a3ac9ccbe2dfb6ab2f913c6952b7aa1`。任务中的指标标签由原始 CSV 的独立 Python 日期计算得到，再与 SQL 响应比较数值、分子分母及工单 ID。
- 手册：TAZ Pro 和 Workhorse 两份公开 PDF，各自登记为演示注册版本；项目自编服务记录便签 v1/v2。PDF 来源哈希见 `docs/manual_sources.json`。手册任务核对逻辑文档、版本和物理 PDF 页码。厂商手册的 `PROBE FAIL CLEAN NOZZLE` 处理段落位于 PDF 第 83 页，`MINTEMP` 描述位于第 82 页；便签在第 1 页。
- 检索配置：384 维确定性词项哈希向量 + BM25、RRF `k=60`、词项重排，服务响应 `top_k=3`；基线为同一已发布、适用版本门槛下的 BM25 原始前 3 条。没有调用托管模型或生成式模型。
- 环境：Python 3.12.3、SQLite 3.45.1、WSL2 Linux。顺序执行，索引已预热。本地响应时间中位数：指标 0.4 ms、文档 111.21 ms、混合 117.55 ms；不代表生产并发或 QPS。

## 结果

| 检查 | 开发集 | 保留集 | 全部 |
| --- | ---: | ---: | ---: |
| 指标，含明细 ID | 48/48 | 12/12 | 60/60 |
| 文档业务响应引用 | 16/16 | 4/4 | 20/20 |
| 混合数值与引用 | 10/10 | 2/2 | 12/12 |
| 缺条件、无数据、过期与拒答 | 6/6 | 2/2 | 8/8 |
| 自动检查合计 | 80/80 | 20/20 | 100/100 |

原始文档检索另行对照：融合检索 Hit@3 为 **16/20**、MRR@3 为 **0.8000**；BM25 为 **16/20**、**0.3667**。两者在前 3 条均未召回 4 条记录流程便签；服务层搜索更多候选并按任务类型选出适用便签，因此业务响应的文档引用为 20/20。不能把业务响应 20/20 说成原始检索 Hit@3；融合检索在该集上改善了排序位置，没有改善 Hit@3。

## 失败记录与待复核

开发阶段首次运行有 3 条 Workhorse `PROBE FAIL CLEAN NOZZLE` 问题引用 PDF 第 33 或第 80 页，而期望处理段落在第 83 页。原因是相似的喷嘴清洁叙述压过了精确错误码条目；随后在 `manuals/search.py` 增加精确错误码和 `Resolution:` 段落排序权重。固定任务重新运行后开发集 80/80，通过后才运行保留集 20/20。最终报告失败数为 0，不抹去首次失败。

剩余质量边界：任务模板由项目生成并由自动 CSV 参考值校验；目前**没有第二人对 100 例逐条人工确认**。PDF 页码已通过本地提取定位，但正式对外展示前需要另一位评审者逐页核对 20 条文档任务及 12 条混合任务的处理语义、版本边界和措辞。4 条便签在原始前 3 条之外，后续可在开发集上改进召回；若修改任务或排序，必须重新冻结任务并另设新的未触碰保留集。已在临时空数据库和空索引中重建四个手册版本并复跑保留集，结果 20/20；新快照 ID 不同但源数据相同。尚未进行真实用户任务测试、跨浏览器验收或生产负载测试。

## 复现

```bash
.venv/bin/python -m service_operations.evaluation.tasks
.venv/bin/python -m service_operations.evaluate --split development
.venv/bin/python -m service_operations.evaluate --split holdout
.venv/bin/python -m service_operations.evaluate --split all
.venv/bin/pytest -q service_operations/tests
```

完整逐例 JSON 写入 `service_operations/runtime/evaluation/`，文件名包含任务哈希和快照 ID；可审查的精简逐例结果另存于 `examples/m5/evaluation_results.json`。运行时目录不提交；本报告记录这次固定运行的汇总。评审时按 `case_id` 在任务文件与逐例 JSON 中查期望、实际引用和失败码。若任务哈希或数据来源哈希不同，不应直接比较上述数字。

## 回归检查范围

`service_operations/tests` 33/33 通过；功能包 Ruff 与 `compileall` 通过。选取的原项目单元测试共 70 项，63 通过、7 失败；失败都在未被本阶段修改的 `tests/unit/test_protocol_handler.py`：6 项访问当前 MCP SDK 已不存在的 `inputSchema`/`isError` 驼峰属性，另 1 项在默认工具已注册时重复注册。M5 不改变原 MCP 协议实现，故这些失败保留为旧项目兼容性问题，不能声称全仓测试通过。
