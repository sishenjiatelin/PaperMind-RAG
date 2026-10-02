# M2｜手册版本、来源与双索引

## 内容与来源

- 官方资料：LulzBot TAZ Pro 和 TAZ Workhorse User Manual，来源 URL、SHA-256、文件大小和 PDF 页数见 [`manual_sources.json`](manual_sources.json)。两份文件均为 110 个 PDF 物理页，原 PDF 标示 CC BY-SA 4.0。下载文件放在 `service_operations/runtime/manuals/`，不提交进仓库；`fetch` 对照固定哈希核验。索引不修改原 PDF。
- 演示流程：[`notes/`](notes/) 的中文便签是项目自编的虚构记录规则；[`../examples/manuals/`](../examples/manuals/) 中两份英文 PDF 可由 `build_demo_pdfs.py` 重建。它们不是 LulzBot 的维修指引。v1 要求记录设备编号、时间、屏幕原文和优先级；v2 额外要求记录打印材料及 Cura LulzBot Edition 配置名称。
- 官方手册的 `demo-registry-v1` 是**项目注册版本标识**，不是制造商声称的手册修订号。`effective_from` 是本演示设定的业务生效日，不是厂商发布日期。真实企业场景须由文控人员确认适用版本和生效区间。

## 复现

在仓库根目录执行：

```bash
python service_operations/examples/manuals/build_demo_pdfs.py
python -m service_operations.manuals.cli fetch
python -m service_operations.manuals.cli index
python -m service_operations.manuals.cli list
python -m service_operations.manuals.cli search --model TAZ_PRO --fault-code NOZZLE_WIPE --as-of 2026-09-28T00:00:00+08:00 --query 'NOZZLE_WIPE printing material Cura profile'
python -m service_operations.manuals.cli search --model TAZ_PRO --fault-code NOZZLE_WIPE --as-of 2026-06-01T00:00:00+08:00 --query 'NOZZLE_WIPE original display priority'
.venv/bin/python -m pytest -q service_operations/tests/test_m2.py
```

上述官方 PDF 的哈希分别为 `62672f7c8c2e71035d87f2c98a6e790364d1ea9bf88b741475bd35d9f3e7d940`（TAZ Pro）和 `1bb8b35e2fa99c3866cbe717be2781d5e52a0efb0d2d25dacc2fc5ac22f91af5`（TAZ Workhorse）。网络来源内容如更新，下载会因哈希不一致而停止，应先核对新来源并创建新版本。

## 数据和索引规则

1. `logical_doc_id` 指业务上的同一份手册，`version` 指业务版本，`source_hash` 指物理 PDF 字节；同一版本不能偷偷替换内容。生效时间必须带时区，统一转 UTC，采用 `[effective_from, effective_to)` 半开区间。同一逻辑手册的有效区间不能重叠。
2. 注册表位于 `service_operations/runtime/manual_index/registry.db`。版本先进入 `staged`，PDF 逐页抽取并在页内分块，Chroma 和 BM25 都建好后才设为 `published`。失败时设为 `error` 并清理两个索引，可重试。删除先设为 `deleting`，再删除该版本的两个索引，最后移除注册记录；清理失败可重试。
3. 每个物理版本使用独立 Chroma collection 和 BM25 文件。查询先按型号、故障分类及时间从注册表选出有效且已发布的版本，然后才分别在选中版本上做 Dense 和 BM25 Top-K，最后以 RRF 融合，并轻微优先选择包含查询词的解释性长段落。旧版文本再相似也不能进入当前候选。返回项包含逻辑 ID、版本、PDF 物理页码、来源、哈希和生效区间。
4. 当前 Dense 分支使用项目内置的**确定性词项哈希向量基线**，无需网络模型；它只体现词项相似度，不代表真正的语义嵌入。后续可通过 `HashEmbedder` 接口替换真实 embedding 服务，并在独立任务集上比较。BM25 复用原项目索引器与分词器。
5. 原摄取历史库现在按 `(file_hash, collection)` 去重；启动时自动迁移旧的全局哈希主键，保留已有记录。同一 PDF 可分别进入不同 collection，删除一个 collection 的记录不影响另一个。

## 已验证边界与限制

- 回归用例故意让旧版包含更强查询关键词，仍只返回当前 v2；历史日期返回 v1。旧版删除后 v2 的 Chroma/BM25 均保留。
- 双索引建造中注入 BM25 失败时，注册表不可查询该版本；再次导入可以恢复。
- PDF 物理页码直接来自解析器，可能不同于页面上印刷的页码。抽取文本中的连字和空格由 PDF 本身决定，建议在 M5 任务集上人工核对引用定位和检索质量。
- 原项目的摄取 Trace 仍有原文记录路径；本阶段的手册索引器不调用该路径。将来若改用原摄取 Pipeline 处理手册，应先完成 Trace 原文脱敏。
