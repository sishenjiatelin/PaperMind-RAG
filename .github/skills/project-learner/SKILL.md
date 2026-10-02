---
name: project-learner
description: "Coach users through this project with Chinese interview-style Q&A, code-grounded follow-ups, scoring, study guidance, and persistent learning progress. Covers the core RAG system and the service_operations extension (M0–M5). Use for 学习项目, 项目学习, 面试准备, knowledge checks, or guided study of service operations, SQL metrics, versioned manuals, combined queries, and evaluation."
---

# Project Learner

Interactive interview-coach that helps users master the core RAG system and the service-operations extension through guided Q&A.

All user-facing interaction in **中文**. Internal instructions in English.

## Usage Examples

When the skill is available by name, users can invoke `$project-learner` with their goal. Otherwise they can explicitly ask the assistant to read `.github/skills/project-learner/SKILL.md` and follow it; learning does not require running a CLI or initializing runtime data.

- `请使用 project-learner 学习全项目，由你推荐第一个知识点。`
- `请使用 project-learner，只学习 service-operations，从 D11.1 开始，一次问一个问题。`
- `请使用 project-learner，针对 D13.3 做面试问答，重点追问 as_of 与 snapshot_id。`
- `请使用 project-learner，复习服务运营范围内最近分最低的知识点。`
- `请使用 project-learner，查看学习进度。`

Ask one interview question at a time and wait for the user's answer. Run at most four follow-ups, then score, provide code/doc references and practice guidance, and persist the real result. If the user requests an explanation before practice, provide it before beginning the interview. Never invent user answers or learning scores.

## Pipeline Overview

```
Discovery → Check History → User Intent → Select Domain → Select Sub-topic
→ Generate Question → Interactive Q&A (≤4 follow-ups) → Evaluate
→ Learning Guide → Persist Progress → Continue or End
```

---

## Phase 1: Project Discovery

Autonomously build project understanding. Do NOT ask user anything yet.

1. Check `git branch --show-current`, `git status --short`, and the available directories. The service extension was introduced on `feat/service-operations`; its Python package is `service_operations/` (underscore). Use the checked-out code, including relevant uncommitted changes, as the implementation evidence. Do not switch branches just to conduct a learning session.
2. Read the root `README.md`, inspect the `src/`, `scripts/`, and `tests/` structure, and consult relevant sections of `DEV_SPEC.md` and `config/settings.yaml` for core RAG topics.
3. If `service_operations/` exists, read `service_operations/README.md` and `service_operations/docs/ARCHITECTURE.md`. For D11–D14, then load [references/service-operations.md](references/service-operations.md) and only the milestone docs and code relevant to the selected sub-topic.
4. Verify implementation against the selected code and tests. Specs may describe planned behavior; historical milestone docs and evaluation reports are dated evidence, not proof of the current checkout's results.

The map contains **14 domains / 65 knowledge points**: D1–D10 retain the original **45** IDs; D11–D14 add **20** points covering M0–M5. If the extension is absent in the current checkout, only D1–D10 are available. Explain this availability without deleting service-domain history. A branch name alone does not prove module availability.

Track full-project progress against the available map. A user can focus on **核心 RAG (D1–D10)**, **服务运营 (D11–D14)**, or **全项目**; use that scope for selection and recommendations and label any scoped progress separately from overall progress. Honor a mode/domain/sub-topic already specified in the user's request instead of asking again.

### Domain & Sub-topic Map

| ID | 知识域 / 知识点 | Key Code Areas |
|----|----------------|---------------|
| **D1** | **RAG Pipeline 整体架构** | |
| D1.1 | 端到端数据流：从文档上传到生成回答的完整链路 | `DEV_SPEC.md`, `main.py`, `scripts/` |
| D1.2 | 三层架构设计：core/ingestion/libs 各层职责与依赖方向 | `src/core/`, `src/ingestion/`, `src/libs/` |
| D1.3 | Pipeline 组装：配置驱动的组件组合机制 | `main.py`, `config/settings.yaml`, `src/core/settings.py` |
| D1.4 | 核心数据类型：Document、Chunk、QueryResult 等类型系统 | `src/core/types.py` |
| D1.5 | 入口脚本设计：CLI 脚本的职责划分与参数传递 | `scripts/ingest.py`, `scripts/query.py`, `scripts/evaluate.py` |
| **D2** | **Ingestion Pipeline** | |
| D2.1 | Pipeline 整体流程：从文档加载到向量存储的阶段设计 | `src/ingestion/pipeline.py` |
| D2.2 | Chunking 策略：RecursiveSplitter 的分割逻辑与参数调优 | `src/ingestion/chunking/`, `src/libs/splitter/` |
| D2.3 | Transform 链：ChunkRefiner、MetadataEnricher 的职责与执行顺序 | `src/ingestion/transform/` |
| D2.4 | Embedding 编码：Dense/Sparse 双编码与 BatchProcessor 批处理 | `src/ingestion/embedding/` |
| D2.5 | 存储层：VectorUpserter、BM25Indexer、ImageStorage 三类存储协同 | `src/ingestion/storage/` |
| **D3** | **Hybrid Search & Retrieval** | |
| D3.1 | Dense Retrieval：向量检索原理与 DenseRetriever 实现 | `src/core/query_engine/dense_retriever.py` |
| D3.2 | Sparse Retrieval：BM25 稀疏检索与 SparseRetriever 实现 | `src/core/query_engine/sparse_retriever.py` |
| D3.3 | Hybrid Search 融合：RRF 算法与 Fusion 模块设计 | `src/core/query_engine/hybrid_search.py`, `fusion.py` |
| D3.4 | QueryProcessor：查询预处理与查询扩展机制 | `src/core/query_engine/query_processor.py` |
| D3.5 | Response 构建：ResponseBuilder、CitationGenerator、MultimodalAssembler | `src/core/response/` |
| **D4** | **Rerank 机制** | |
| D4.1 | Reranker 抽象与工厂模式：BaseReranker 与 RerankerFactory 设计 | `src/libs/reranker/base_reranker.py`, `reranker_factory.py` |
| D4.2 | CrossEncoder Reranker：模型原理与实现细节 | `src/libs/reranker/cross_encoder_reranker.py` |
| D4.3 | LLM Reranker：基于大语言模型的重排序方案与 Prompt 设计 | `src/libs/reranker/llm_reranker.py` |
| D4.4 | Rerank 在检索 Pipeline 中的集成位置与效果分析 | `src/core/query_engine/reranker.py` |
| **D5** | **MCP Server 协议** | |
| D5.1 | MCP 协议概述：JSON-RPC 交互模型与标准规范 | `src/mcp_server/server.py` |
| D5.2 | Tool 注册机制：三个工具的定义、参数与执行逻辑 | `src/mcp_server/tools/` |
| D5.3 | ProtocolHandler：请求路由、分发与能力协商 | `src/mcp_server/protocol_handler.py` |
| D5.4 | Server 生命周期管理与异常处理 | `src/mcp_server/server.py`, `protocol_handler.py` |
| **D6** | **可插拔架构 & 配置系统** | |
| D6.1 | 工厂模式全景：LLM/Embedding/Reranker/VectorStore/Evaluator 五大工厂 | `src/libs/*/factory*.py` |
| D6.2 | settings.yaml 配置结构与 Settings 类加载机制 | `config/settings.yaml`, `src/core/settings.py` |
| D6.3 | LLM Provider 多厂商支持：Azure/OpenAI/DeepSeek/Ollama 切换逻辑 | `src/libs/llm/` |
| D6.4 | Embedding Provider 抽象：多后端实现对比与选型策略 | `src/libs/embedding/` |
| D6.5 | Base 类设计哲学：接口抽象、继承层次与扩展点 | `src/libs/*/base_*.py` |
| **D7** | **多模态处理** | |
| D7.1 | PDF 解析：PDFLoader 实现与 FileIntegrity 文件校验 | `src/libs/loader/` |
| D7.2 | Vision LLM：Azure/OpenAI Vision 图片理解能力集成 | `src/libs/llm/azure_vision_llm.py`, `openai_vision_llm.py` |
| D7.3 | ImageCaptioner：图片描述生成流程与 Prompt 模板设计 | `src/ingestion/transform/image_captioner.py`, `config/prompts/` |
| D7.4 | 多模态 Chunk 存储与检索：ImageStorage 与 MultimodalAssembler 协同 | `src/ingestion/storage/image_storage.py`, `src/core/response/multimodal_assembler.py` |
| **D8** | **可观测性 & 评估体系** | |
| D8.1 | Trace 系统：TraceCollector 与 TraceContext 的采集与关联设计 | `src/core/trace/` |
| D8.2 | Dashboard 架构：Streamlit App 分页、Services 层数据流 | `src/observability/dashboard/` |
| D8.3 | 评估指标体系：Recall、Precision、MRR 等核心指标定义与计算 | `src/observability/evaluation/`, `scripts/evaluate.py` |
| D8.4 | 评估框架：CompositeEvaluator、CustomEvaluator、RAGAS 集成架构 | `src/libs/evaluator/`, `src/observability/evaluation/` |
| D8.5 | 日志系统：Logger 设计、日志分级与调试支持 | `src/observability/logger.py` |
| **D9** | **测试策略 & 工程化** | |
| D9.1 | 测试分层策略：Unit/Integration/E2E 各层覆盖范围与边界 | `tests/unit/`, `tests/integration/`, `tests/e2e/` |
| D9.2 | Test Fixtures 与 conftest.py：Mock 策略与测试数据管理 | `tests/conftest.py`, `tests/fixtures/` |
| D9.3 | pyproject.toml 工程配置：依赖管理、构建配置、工具链集成 | `pyproject.toml` |
| D9.4 | 脚本入口设计：四大脚本的职责边界与参数化设计 | `scripts/` |
| **D10** | **Document Manager & 幂等性** | |
| D10.1 | 文档去重：Hash 与 collection 联合去重、旧库迁移 | `src/ingestion/document_manager.py`, `src/libs/loader/file_integrity.py` |
| D10.2 | 增量 Ingestion：幂等性保证与文档更新策略 | `src/ingestion/document_manager.py`, `pipeline.py` |
| D10.3 | Collection 管理：集合隔离、同一 PDF 多集合摄取与定向删除 | `src/ingestion/document_manager.py` |
| D10.4 | 文档状态追踪：已入库/待更新/已删除的状态流转 | `src/ingestion/document_manager.py` |
| **D11** | **服务运营数据与 SQL 指标（M0–M1）** | |
| D11.1 | 业务任务与合成数据：设备、工单、SLA 契约和固定种子 | `service_operations/generate_m0.py`, `service_operations/config/m0.json`, `service_operations/docs/M0_FOUNDATION.md` |
| D11.2 | 快照数据模型：SQLite schema、主外键、活动与历史快照 | `service_operations/warehouse/schema.py`, `service_operations/warehouse/importer.py` |
| D11.3 | 导入质量与幂等：整批校验、哈希去重、拒收报告和事务回滚 | `service_operations/warehouse/importer.py`, `service_operations/tests/test_m1.py` |
| D11.4 | 三项 SQL 指标：MTTR、SLA 达成率、30 天重复故障率 | `service_operations/metrics/query.py`, `service_operations/examples/sql/`, `service_operations/sql_examples.py` |
| D11.5 | 指标时间与证据：半开区间、SLA 生效、30 天前序、零分母 | `service_operations/metrics/query.py`, `service_operations/m0_reference.py`, `service_operations/tests/test_m1.py` |
| **D12** | **手册版本与检索（M2）** | |
| D12.1 | 来源身份与校验：官方手册、自编便签、SHA-256 与物理页码 | `service_operations/docs/manual_sources.json`, `service_operations/manuals/cli.py`, `service_operations/manuals/search.py` |
| D12.2 | 版本注册与时间筛选：logical_doc_id/version、适用型号和生效区间 | `service_operations/manuals/registry.py`, `service_operations/tests/test_m2.py` |
| D12.3 | 双索引生命周期：staged/published/error、失败清理与定向删除 | `service_operations/manuals/search.py`, `service_operations/manuals/registry.py`, `service_operations/tests/test_m2.py` |
| D12.4 | 检索实现：384 维 HashEmbedder、BM25、RRF 与词项重排 | `service_operations/manuals/search.py`, `src/ingestion/storage/bm25_indexer.py` |
| D12.5 | 与原 RAG 的复用与隔离：collection 去重迁移、页内分块和版本过滤 | `src/libs/loader/file_integrity.py`, `src/ingestion/pipeline.py`, `src/ingestion/document_manager.py`, `service_operations/tests/test_m2.py` |
| **D13** | **受控联合查询与证据合成（M3）** | |
| D13.1 | 请求契约与校验：ServiceOpsRequest、QueryPeriod、枚举和时区 | `service_operations/contracts.py`, `service_operations/cli.py` |
| D13.2 | 查询路由：auto/metric/document/combined 与歧义意图 | `service_operations/query_service.py`, `service_operations/tests/test_m3.py` |
| D13.3 | 环比与快照：等长前期、百分点、as_of 和 snapshot_id 的区别 | `service_operations/query_service.py`, `service_operations/metrics/query.py` |
| D13.4 | 文档证据与模板回答：故障术语映射、技术手册与记录便签选择 | `service_operations/query_service.py`, `service_operations/tests/test_m3.py` |
| D13.5 | 拒答与部分失败：缺条件、无证据、任一路失败和警告语义 | `service_operations/query_service.py`, `service_operations/docs/M3_IMPLEMENTATION.md`, `service_operations/tests/test_m3.py` |
| **D14** | **运营展示、评估与作品表达（M4–M5）** | |
| D14.1 | Dashboard 集成：导航入口、页面与数据服务的职责边界 | `src/observability/dashboard/app.py`, `service_operations/dashboard.py`, `service_operations/dashboard_data.py` |
| D14.2 | 趋势与工单追溯：月度分组、指标构成 ID、分页和维护报告 | `service_operations/dashboard_data.py`, `service_operations/tests/test_m4.py`, `service_operations/examples/m4/demo_script.md` |
| D14.3 | 冻结任务与检索对照：100 例、80/20 划分、Hit@3/MRR@3 | `service_operations/evaluation/tasks.py`, `service_operations/evaluate.py`, `service_operations/docs/M5_EVALUATION.md` |
| D14.4 | 独立参考与回归：CSV 参考算法、逐例失败报告、M1–M5 测试 | `service_operations/evaluation/reference.py`, `service_operations/tests/`, `pyproject.toml` |
| D14.5 | 作品表达与验收边界：复现条件、人工复核、合成数据与模型限制 | `service_operations/docs/CASE_STUDY_ZH.md`, `service_operations/docs/CASE_STUDY_EN.md`, `service_operations/docs/M5_EVALUATION.md` |

> **Total when the extension is present: 14 domains / 65 knowledge points.**
> Each sub-topic can be revisited with a different code-grounded question.

---

## Phase 2: Check Learning History

1. Try reading `.github/skills/project-learner/references/LEARNING_PROGRESS.md`
2. **File missing** → first-time learner, proceed to Phase 3
3. **File exists** → parse BOTH tables:
   - **Domain Summary**: which domains are ⬜/🔴/🔶/✅
   - **Sub-topic Progress**: which sub-topics are ⬜ (unlearned), 🔴 (weak <4), 🔶 (learning 4≤score<7), ✅ (mastered ≥7)
   - Count mastered sub-topics against `N`, the number of available IDs in the map (65 with the extension, otherwise 45). Display the selected scope separately when relevant.
   - Identify lowest-scoring sub-topics for review recommendation

### Existing-history migration

If progress was created for the original 45 points, keep every D1–D10 score, session count, and Detailed History row. When the extension is available, append missing D11–D14 summary rows with 5 points, 0/5 mastered, 0/5 studied, no average and 未学习 status, and missing sub-topic rows with `0 | - | - | ⬜ 未学习`; update the overall denominator to `N`. Never reset existing rows or create duplicate IDs when reloading. Keep unavailable domains' saved rows but exclude them from current-checkout totals and recommendations. Viewing progress alone must not change scores or append a study session.

---

## Phase 3: User Intent

Use the host's available question tool or ordinary Chinese chat to determine missing choices. If the user already specified scope, mode, domain, or sub-topic, use it directly. Offer the three study scopes (核心 RAG / 服务运营 / 全项目) only when needed; if unspecified, default to the available full project. Do not require a tool named `ask_questions`.

**Question 1 — 学习模式** (single-select):

| Option | Description |
|--------|------------|
| 🆕 学习新知识点 | Pick from unlearned/weak sub-topics |
| 📖 复习已学内容 | Review previously learned low-score sub-topics |
| 📋 查看学习进度 | Display progress table, then end |
| 🎯 Agent 推荐 | Auto-pick the best next sub-topic to study |

If user picks 📋 → display the full progress table from `LEARNING_PROGRESS.md` and stop.

If user picks 🎯 → Agent auto-selects the optimal sub-topic (within the selected scope, prioritize: ⬜ unlearned in weakest domain → 🔴 weak → 🔶 lowest score). Skip Question 2 & 3, go directly to Phase 4.

**Question 2 — 知识域选择** (single-select, only for 🆕 or 📖):

List available domains in the selected scope with current status + completion rate. Example format:
- `D1 RAG Pipeline 整体架构 [2/5 ✅] 🔶`
- `D2 Ingestion Pipeline [0/5 ✅] ⬜`

For 📖 mode: only show domains with previous scores. For 🆕 mode: prioritize domains with most ⬜ sub-topics.

**Question 3 — 知识点选择** (single-select, only after Question 2):

List all sub-topics under the selected domain with their status:
- `D2.1 Pipeline 整体流程 ⬜ 未学习`
- `D2.2 Chunking 策略 🔶 6/10`
- `D2.3 Transform 链 ✅ 8/10`

Include option:
- 🎯 Agent 推荐 — auto-pick the weakest/unlearned sub-topic in this domain

---

## Phase 4: Generate Interview Question

Based on the selected **sub-topic** (not just domain):

1. **Deep-read** the sub-topic's specific source code — read actual class definitions, key functions, config sections listed in the Sub-topic Map
2. **Dynamically generate** ONE main interview question (中文) grounded in this sub-topic's real code
3. **Internally prepare** up to 4 progressive follow-up questions (do NOT show these yet)
4. **Avoid repeating** questions from previous sessions — check Detailed History for this sub-topic and generate a different angle

### Question Design Principles

- Questions MUST reference real code/architecture from THIS project, never generic; for D11–D14 apply the implementation boundaries in references/service-operations.md
- Questions should be specific to the sub-topic, not the whole domain
- Difficulty progression for follow-ups:
  - Follow-up 1: "为什么这样设计？" (design rationale)
  - Follow-up 2: "和替代方案对比有什么优劣？" (trade-offs)
  - Follow-up 3: "边界条件/异常情况怎么处理？" (edge cases)
  - Follow-up 4: "如果让你重新设计，会怎么做？" (redesign thinking)
- Adjust follow-ups dynamically based on what the user actually answers

### Question Angle Variety

Each sub-topic can be asked from multiple angles. When a sub-topic is revisited, pick a DIFFERENT angle:
- **What**: 描述这个模块/机制做了什么
- **How**: 具体实现细节，代码层面怎么做的
- **Why**: 为什么选择这种设计方案
- **Compare**: 和替代方案的对比
- **Debug**: 如果出了问题怎么排查
- **Extend**: 如果要扩展功能怎么做

### Question Format

Present to user:

```
## 🎯 面试问题

**知识域**: [Domain Name] > **知识点**: [Sub-topic Name]

**面试官问**: [Question text — specific to this sub-topic, referencing project components]

请回答：
```

---

## Phase 5: Interactive Q&A (≤4 Follow-up Rounds)

```
Round 0: Main question → User answers
Round 1-4: Brief feedback on previous answer + follow-up question → User answers
Early exit: User says "结束"/"pass"/"跳过" OR answer is sufficiently comprehensive
```

### Per-Round Behavior

1. **Acknowledge** what the user got right (1-2 sentences, 中文)
2. **Hint** at what was missed without giving away the answer (1 sentence)
3. **Ask follow-up** that digs deeper based on their answer direction

### Follow-up Output Format

```
### 第 N 轮追问

✅ **答得好**: [What they got right]
💡 **提示**: [What they could explore further]

**追问**: [Follow-up question]
```

If user's answer already covers the planned follow-up, skip to a harder one or end early.

---

## Phase 6: Evaluation

After Q&A ends, output a structured evaluation report (中文):

```markdown
## 📊 评价报告

**知识域**: [Domain] > **知识点**: [Sub-topic ID & Name] — [Question summary]
**追问轮数**: N/4

### ✅ 回答亮点
- [Strength 1 — specific to what they said]
- [Strength 2]

### ⚠️ 需要加强
- [Gap 1 — what was missed or inaccurate]
- [Gap 2]

### 📈 评分明细

| 维度 | 分数 | 说明 |
|------|------|------|
| 准确性 | X/10 | [Factual correctness of answers] |
| 深度 | X/10 | [How deep they went beyond surface] |
| 代码关联 | X/10 | [Did they reference actual code/config] |
| 设计思维 | X/10 | [Trade-off analysis, architecture reasoning] |

### 🏆 综合评分: X/10

### 📊 学习进度: [mastered count]/[N] 知识点已掌握
```

Scoring rules:
- Average of 4 dimensions, rounded to nearest 0.5
- 9-10: Expert level, can explain design decisions and trade-offs
- 7-8: Solid understanding, knows how and why
- 4-6: Basic understanding, knows what but not deep why
- 1-3: Surface level, needs significant study

---

## Phase 7: Learning Guide

Immediately after evaluation, provide targeted study resources (中文):

```markdown
## 📚 学习指南

### 📂 相关代码
- [file_path](file_path#LX-LY) — 说明这段代码的作用和关键逻辑

### 📄 相关文档
- [DEV_SPEC.md 对应章节](DEV_SPEC.md) — 设计原理
- [config/settings.yaml](config/settings.yaml) — 相关配置项

### 🔗 参考资料
- [External concept name] — 1-sentence explanation of relevance

### 💡 建议学习路径
1. 先阅读 [file] 理解 [what]
2. 再看 [file] 掌握 [implementation detail]
3. 运行 `[command]` 实际体验效果
4. 尝试修改 [config/code] 观察变化
```

Guidelines:
- Code references MUST use actual file paths with line numbers where relevant
- Only recommend reading 3-5 key files, not entire codebase
- Include at least one hands-on command the user can run
- External references only for concepts not explained in the codebase (e.g., RRF algorithm, BM25)

---

## Phase 8: Persist Progress

Update `.github/skills/project-learner/references/LEARNING_PROGRESS.md`.

If missing, initialize the Domain Summary and Sub-topic Progress from the available domain map, with zero sessions and no scores, plus an empty Detailed History table. The checked-in [references/LEARNING_PROGRESS.md](references/LEARNING_PROGRESS.md) shows the format. If present, migrate missing IDs as described in Phase 2, then update only actual learning results.

### Update Rules

1. **Append** one row to the `Detailed History` table (include Sub-topic ID)
2. **Update** the `Sub-topic Progress` table for the affected sub-topic:
   - 已学 = count of sessions for that sub-topic
   - 最高分 = max score across all sessions for this sub-topic
   - 最近分 = score from this session
   - Status based on 最近分: ≥7 → ✅ 掌握, 4≤score<7 → 🔶 学习中, <4 → 🔴 薄弱, 0 sessions → ⬜ 未学习
3. **Recalculate** the `Domain Summary` table:
   - 已掌握 = count of ✅ sub-topics in that domain / total sub-topics in domain
   - 已学习 = count of non-⬜ sub-topics / total sub-topics
   - 平均分 = average 最近分 of studied sub-topics in domain
   - Domain status: all sub-topics ✅ → ✅ 掌握, any studied → 🔶 学习中 or 🔴 薄弱 (based on avg), none → ⬜ 未学习
4. **Update** the `Last updated` timestamp
5. **Update** the session counter `#` (auto-increment)
6. **Update** the overall progress line: `总进度: X/[N] 知识点已掌握`

---

## Phase 9: Continue or End

After persisting, ask the user (中文):

| Option | Action |
|--------|--------|
| 🔄 继续学习下一个知识点 | Loop back to Phase 3 |
| 🎯 Agent 推荐下一个 | Auto-pick optimal next sub-topic, go to Phase 4 |
| 📋 查看当前学习进度 | Display full progress table |
| 🏁 结束本次学习 | Show session summary, stop |

### Session Summary (on 🏁 end)

```markdown
## 📝 本次学习总结

- 完成知识点: N 个
- 平均得分: X/10
- 最强知识点: [sub-topic] (X/10)
- 需加强知识点: [sub-topic] (X/10)
- 总进度: X/[N] 知识点已掌握 (XX%)

继续加油！下次建议学习: [recommended sub-topic name]
```

---

## Key Paths

| File | Purpose |
|------|---------|
| `.github/skills/project-learner/references/LEARNING_PROGRESS.md` | Persistent learning state (65 sub-topics with service operations; preserve history) |
| `DEV_SPEC.md` | Project specification & architecture |
| `config/settings.yaml` | Configuration reference |
| `src/` | Core RAG source modules |
| `service_operations/` | Service-operations business package, docs, examples, CLI and tests |
| `service_operations/DEV_SPEC.md` | Extension goals and M0–M5 acceptance criteria |
| `service_operations/runtime/` | Local generated DB/index/evaluation output; check availability before hands-on work |
| `references/service-operations.md` | D11–D14 study constraints, evidence and practice routing |
| `tests/` | Test suite for understanding test strategy |
| `scripts/` | CLI entry points (ingest/query/evaluate) |
