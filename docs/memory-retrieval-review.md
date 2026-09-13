# Memory retrieval 诊断与最小改进方案

审查日期：2026-09-07。输入：`context_selection_prod_router_planner_fixed_v2.zip` 的 32 条选择结果，以及两个公开仓库当前代码。此次完成静态审查、32 条结果逐项核对、原函数的隔离复现；没有修改远端仓库或生产数据库，没有重跑生产 benchmark。

## 结论

值得拆模块，但第一步只拆聊天检索链路。当前效果差有明确的行为原因：会话标题把无关消息带进候选集，正文证据被截断，查询泛化或丢失指代，而 router 漏掉一些需要语义记忆的请求。整体搬文件不会自动解决这些问题。

建议顺序：**保存可追踪基线 → 抽出聊天检索模块 → 修复命中证据与候选选择 → 再做重排、分组去重和更好的 query planning**。使用现有 evidence contract，不另起一套候选结构。

## 审查版本与范围

- [chat-proxy：d690761](https://github.com/njzfjiang/chat-proxy/tree/d690761afb61292e745debc818d4835b97176c8a)，提交时间 2026-09-07 03:29 UTC。
- [kmlog_search：7b5e0e6](https://github.com/njzfjiang/kmlog_search/tree/7b5e0e663a712b34a8bd9b94eee743cd6009461e)，提交时间 2026-09-06 13:43 UTC。
- 重点阅读 context builder、planner、source adapters、evidence contracts、benchmark、corpus probe、SQLite search、HTTP search endpoint、FTS 建库分类规则。
- ZIP 未记录上述代码提交、生产服务版本或 DB 指纹，不能认定它就是当前 HEAD 的运行结果。将当前 planner 对 32 条输入离线执行，`sources` 和 `search_query` 均与 CSV 一致；但 CSV 的 retrieval items 没有当前 exporter 已包含的 `planner_required_matches` / `planner_optional_matches`，因此完整运行链路仍待锁版。
- 本地没有用户生产 DB，也没有每条样本的正确目标记录。以下严格区分结果中可见现象、当前代码机制、合成数据复现和待验证假设。

## Benchmark 实际测到了什么

| 项目 | 结果 | 能说明什么 |
|---|---:|---|
| 构建成功 | 32/32 | 本次链路调用没有报告错误 |
| 标注需要 episodic/semantic 的请求有历史候选 | 18/23，78.3% | 候选非空率，不能当 Recall@k 或准确率 |
| 上述请求有任一非 recent 来源候选 | 18/23 | curated 未把另外 5 条空结果补上 |
| 上述请求有 curated 候选 | 12/23 | 仅表示来源选择非空 |
| 历史检索相关性人工标签 | 0/32 | 目前无法算可靠的 precision / recall |
| `none` 样本有历史/curated 候选 | 0/6 | 长期检索负对照表现符合标签 |
| 同一批 `none` 样本有 recent 消息 | 6/6，每条 6 条消息 | `no_context` 指标实际没有包含 recent；需要明确标签语义 |
| seed 按内容重新映射 ID | 26/32 | 行号随导入变化，后续 gold 应使用稳定原始 ID |
| 报告中的 future leaks | 0 | 不等于所有来源均为历史快照；curated 明确使用当前快照 |

另外，benchmark 把 episodic 和 semantic 合并为 expected_retrieval。semantic 请求并不必然需要原始聊天搜索，可能只需要 core / mother / reviewed；不能为了提升这个比例而强迫每个请求搜聊天。应按期望来源和所需证据分别评估。

## 已定位的机制

### 1. 会话标题与正文混合打分，候选在过早截断时被挤掉

[search_sqlite.py](https://github.com/njzfjiang/kmlog_search/blob/7b5e0e663a712b34a8bd9b94eee743cd6009461e/servers/search_sqlite.py) 的 `_search_messages_single` 同时匹配正文和 conversation_title。无空格查询的标题 LIKE 加分可叠成 1.2，正文 LIKE 加分叠成 0.6；FTS 又同时索引这两列。一个长会话里，标题命中会使其中大量无关消息也入围。

这里不是简单的“BM25 正负号写反”：代码已经对 bm25 取负。问题是字段证据、候选截断和排序混在一起。中文 `unicode61` 的词边界与子串匹配不同，正文 LIKE 命中与 FTS 命中还会走不同计分路径。

本地采用仓库实际函数和实际 unicode61 schema 建临时库：20 条正文“今天聊晚饭。”、标题“失眠”的消息；1 条标题“日记”、正文“昨晚因为失眠到三点才睡着”的消息。查询“失眠”：top-5 全是晚饭记录，真正正文记录排第 21；两类分数约为 1.2000013 与 0.6。

**修复方向：**保留 body/title 分开的命中证据；标题主要帮助定位会话，不能单独证明每条消息相关。先保证正文候选不被标题命中淹没，再评估标题-only 候选的降权或独立通道。不要直接把标题全面删除，用户可能正是按标题回忆会话。

### 2. 160 字预览被误当作完整检索证据

同一 SQL 只返回 `substr(messages.content, 1, 160)`；`_row_token_text` 用这个预览和标题计算 token hits。proxy 的 `_rerank_kmlog_results` 也只看预览和标题。正文后部的真实命中会丢失。

本地复现：在第 160 字后放入 `FISTA`，后端能搜到该记录，但返回 token_hits=0；proxy 对 `FISTA project` 做实体过滤后，记录消失，`entity_filtered=1`。

**修复方向：**后端基于完整正文生成 matched terms / match spans 和命中位置周边的 excerpt；保留旧 `content_preview` 兼容现有接口。proxy 使用明确的正文证据进行过滤，不把展示用前缀当全文。FTS 与 LIKE fallback 都要能生成证据。

注意：CSV 中 token_hits=0 不等于全文完全无关，预览看不到关键词也不能证明全文没有；人工评估应获得命中片段或完整证据。

### 3. Planner 识别了主题，却没有稳定保住所需事件

[retrieval_planner.py](https://github.com/njzfjiang/chat-proxy/blob/d690761afb61292e745debc818d4835b97176c8a/chat_proxy/retrieval_planner.py) 使用最后一条请求做规则规划。`build_web_chat_context` 虽已读取 recent，仍向聊天 planner 只传当前 user_text。因此“这门课”“讨论这个”等不能利用前文消歧。

- 样本 10403：“吃完药……学校医疗保险”只生成“药”。后端 fallback tokenizer 丢弃长度小于 2 的 token，因此 token_hits 没有区分力，但单字仍可走 phrase/LIKE 搜索；不是完全搜不了。
- 样本 17699：西梅树、滑冰与母亲的回忆被表示为“之前”“那时候”加整段长句。长句很难精确匹配，泛词带回无关对话。
- 样本 8422：谈剧情推进和证据，却主要搜“之前 / deepseek”，返回讨论其他模型的聊天。
- Planner 最多输出 10 项，后端 tokenizer 默认最多保留 8 项；空格复合实体在传输时又会被拆开，接口并未保留“实体组”语义。

**修复方向：**将 retrieval intent 与搜索实体分开；“之前”用于判意图，避免作为独立高权重内容词。给 planner 有界 recent 输入以解析明确指代，同时防止闲聊污染；传结构化实体/同义词组、支持词和事件线索。不要把 32 条样本词句逐个塞进词表作为主要方案。

### 4. 当前已经有规则重排和去重，但覆盖范围有限

[context_builder.py](https://github.com/njzfjiang/chat-proxy/blob/d690761afb61292e745debc818d4835b97176c8a/chat_proxy/context_builder.py) 的 `_rerank_kmlog_results` 已做：特定 synthetic 前缀过滤、空白归一化后的预览去重、课程实体过滤、按实体长度与 optional matches 重排。

限制如下：

- required_terms 主要由课程实体产生；“牙疼 智齿 发炎”没有相应实体约束，手部发炎也能通过。
- 有多个 required_terms 时，只要求任意一个命中，且主要按最长实体长度排；同一请求涉及多个课程时可能被单一课程占满。
- 只有 required_terms 非空时才增加 fetch_limit，最高仍为 20；其他类型通常拿配置的 5 条再处理。
- 后端 token 模式每词先截断到至少 10 条，再融合；最终候选中没有的记录，后续重排无法恢复。
- 预览 exact dedup 不能覆盖语义重复；反过来，两条前缀相同、后文不同的有效记录可能被误合并。

因此“完全没有 rerank/去重”不适用于当前仓库；更准确地说是已有局部启发式处理，缺少统一的候选证据、可靠的召回池和事件级多样性控制。

### 5. 类型过滤可能把工程历史排除在外

[build_sqlite_fts.py](https://github.com/njzfjiang/kmlog_search/blob/7b5e0e663a712b34a8bd9b94eee743cd6009461e/import_scripts/build_sqlite_fts.py) 在未显式提供 kind 时，根据标题和正文的 sqlite、database、schema 等词把消息标为 meta。proxy 请求固定 `kinds=['chat']`。

合成输入“今天给 sqlite 检索接口加了过滤。”确实被分类为 meta。**这验证了规则冲突，不证明生产 DB 中某条目标已因此丢失。** 应先统计失败目标的 kind，再区分“真实工程讨论”与“系统元数据”，不宜直接放开全部 noise/meta。

### 6. Curated 来源有边界，但还不是统一证据流水线

Mother 在四条 health 样本中都选出 C.1/C.2/C.3，属于领域路由，是否真正帮助当前问题仍需逐项判断。失眠、胃胀还触发同一个安全护栏 WB：可能有意义，但不能用非空数作为有效性证明。

J / reviewed 目前是 selection-only。reviewed 先取最多 50–200 条 active 条目再本地关键词排序，未分页遍历全部；条目增多后，较早但相关的记忆可能进不了候选池。此为规模增长风险，本次没有证据证明它造成当前失败。

已有 `retrieval_contracts.py`、`retrieval_source_adapters.py` 是好的拆分起点。后续聊天来源也应保留 source_id、role、时间、来源引用、证据类型，而不是把 assistant 历史回复默认当成已验证用户事实。

## 32 条样本逐项审阅

ID 为 CSV 当前解析后的 message_id。以下是依据查询与返回预览的诊断笔记，**不是人工 gold relevance 标签**；未见完整正文或目标记录的条目不判定召回成败。

| ID | 类别 | 观察与下一核查点 |
|---|---|---|
| 831 | infra | 路径、记忆整理相关记录可见；确认是否提供上次配置变更，而非只重复文件名 |
| 4249 | infra | mem0 历史与宽泛 vault 聊天混合；需标注具体导入/噪音事件 |
| 4239 | infra | 有 Supabase GET/POST 前情，也混入 health MCP 与泛 db 聊天 |
| 5705 | infra | 前排含多条近似“整理记忆”提醒；适合事件级去重验收 |
| 985 | course | fairness 占满结果，多课程的作业状态没有均衡覆盖 |
| 10497 | course | cloud 相关性可见，但项目架构、课程安排、闲聊混排 |
| 10891 | course | 有 LASSO/ISTA/FISTA/ADMM 项目背景，前排仍偏课程日程 |
| 12762 | course | 未解析“这门课”；有紧邻相关消息 12761，也混入其他课程/闲聊 |
| 8112 | daily | recent-only，符合标签 |
| 24900 | daily | 长期来源为空；none 仍保留 recent |
| 10659 | daily | 长期来源为空；none 仍保留 recent |
| 20206 | daily | recent-only，符合标签 |
| 142 | intimate | 长期来源为空；none 仍保留 recent |
| 3934 | intimate | 期望 semantic，route=social，所有长期来源为空；先明确需要哪条稳定记忆 |
| 214 | intimate | 期望 semantic/rule，route=unclassified；不宜简单强制聊天检索 |
| 3536 | intimate | 模型恐惧/自我关闭的问题未触发对应语义来源 |
| 522 | recollection | deepseek 泛词主导，未看到灰度测试/下架事件证据 |
| 17699 | recollection | “之前/那时候”带回无关预览；先核实树/滑冰事件在 cutoff 前是否已有记录 |
| 8422 | recollection | 查询丢掉剧情/证据需求，结果偏 deepseek 关系闲聊 |
| 33394 | recollection | 有 Qdrant 和旧检索问题，也混入课程 keyword search 记录 |
| 10741 | philosophy | presentation 触发课程路径，结果混入演讲任务；需识别是在讨论自我呈现 |
| 5080 | philosophy | 期望 semantic，unclassified，长期来源为空 |
| 18108 | philosophy | 有自我/伦理前情，也有无关预览；需完整证据判断排序 |
| 24510 | philosophy | “道/从无到有”未命中现有词表，长期来源为空 |
| 146 | quote | 长期检索负对照通过；none 仍有 recent |
| 3496 | quote | 长期检索负对照通过；none 仍有 recent |
| 5653 | quote | recent-only，符合标签 |
| 29212 | quote | 长期检索负对照通过；none 仍有 recent |
| 31847 | health | “失眠”召回混合调情与健康文本；核查字面症状证据 |
| 11847 | health | “牙疼 智齿 发炎”唯一结果是手部伤口护理，事件/部位不符 |
| 32588 | health | 有胃胀历史；还需区分当前照护、旧危机与归档讨论的用途 |
| 10403 | health | “药”丢掉医疗保险与就医经历，全部 token_hits=0 |

## 最小下一步：两次小改动，分别验收

### 改动一：抽出边界、补齐可观测性，保持检索行为

1. kmlog_search 新建 `servers/message_search.py`，迁移 tokenizer、查询与候选合并/排序函数；`search_sqlite.py` 保留原公共入口转发，避免同时改 HTTP 和 MCP 所有调用。连接工厂显式注入，以便临时 DB 测试，不要让新模块再反向导入旧大文件造成循环。
2. chat-proxy 将 `_kmlog_search_messages` 的网络访问与 `_rerank_kmlog_results` 分离，使用现有 contracts 接入；`context_builder.py` 保留编排和组装职责。此次不同时迁移 mother 写入、reviewed 工作流、WB 全部逻辑。
3. benchmark 输出完整阶段 trace：版本、配置/数据指纹、source plan、发出的 query/terms、backend candidate IDs、过滤原因、重排前后 IDs、budget 前后 IDs、最终注入 IDs。保留 role/title/命中位置，并标注来源是历史快照还是 current_snapshot。
4. 在一份固定数据库上比较拆分前后候选 ID、排序、响应字段；这次只验收行为一致。当前没有生产 DB，不能声称已完成该验收。

### 改动二：只修正文证据与候选池

1. 返回完整正文上的 body/title matched terms 与命中窗口；展示截断不再决定过滤。
2. 将 candidate_limit 与 final_limit 分开；候选池大小根据固定样本与延迟实测选取。不能只增大 LIMIT 而忽略标题噪音和每词早截断。
3. 标题-only 候选独立标记，避免压过明确正文事件证据。先做来源 ID 去重与确切重复正文分组，保留 provenance；语义/事件去重之后单独评估。
4. 保留旧模式开关，比较这一步对证据保留与已知目标排名的影响。不要同时上向量库、新分词器、大模型 planner 和全域 reranker，否则难以归因。

## 验收数据与指标

沿用仓库已经有的 `context_selection_corpus_probe.py` 与 `course_project_known_positive.csv`。后者目前是已知存在前情的 query seeds，不应直接当成完整相关文档 gold。

先为约 8 个代表性请求找到 cutoff 之前的稳定目标 ID/事件簇：选择 11847、17699、8422、5705、10497、10891、12762、10403 作为核查起点。如果某条没有历史证据，将其标为新信息或 corpus-unknown，不强造 gold。

- 路由：应该选哪个来源，实际是否选中。允许没有长期检索需求。
- 召回池：已标注目标进入 pool 的 Recall@pool。
- 选择：最终 Hit@5 / Recall@5；有多级相关性标签时再算 nDCG@5。
- 去重：同一事件是否挤占多个名额，同时检查独立证据是否被误合并。
- 注入：选中证据经过预算和格式化后是否仍可见。
- 历史性：用解析后的时间与稳定消息 ID 校验；当前 curated 快照单独评估。
- 成本：请求次数、延迟与上下文 token；本 ZIP 不足以估算生产改进幅度。

负对照保留 daily / quote 样本，防止为提高召回而让所有闲聊都检索。32 条已用于诊断和规则设计，后续应补独立留出集，不能只对同一批样本反复调参后声称泛化改善。

## 本地复现记录

复现从已下载源码 AST 提取实际函数，避免导入服务时触碰生产资源；SQLite schema 与仓库建库脚本一致，数据库位于临时目录。没有用生产私密数据。

| 检查 | 实际结果 |
|---|---|
| 标题-only 淹没正文命中 | top5=1–5，正文目标 ID21 排第21 |
| 正文第160字后 FISTA | 后端返回 ID22；token_hits=0；proxy entity_filtered=1，最终为空 |
| 单字 query | planner 得“药”，fallback tokens=[] |
| 查询词数量契约 | 10 个空格分隔词，后端只保留前8个 |
| 工程消息 kind | sqlite 讨论在无显式 kind 时被归为 meta |
| 当前 planner 对比 ZIP | 32/32 的 sources 与 search_query 一致 |

这些结果验证了机制；尚未证明修复能把生产集提升到某个分数。下一轮有了固定 DB、版本 trace 和目标标签，才适合给出量化结论。
