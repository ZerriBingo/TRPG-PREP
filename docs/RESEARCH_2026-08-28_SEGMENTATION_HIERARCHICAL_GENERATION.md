# PDF 切分与大事实集分层生成研究

更新时间：2026-08-28  
状态：**研究证据与工程建议，不直接构成产品决策；确认后应写入新的重评/决策记录。**  
研究范围：

1. 扫描/PDF 文档中，机械分页、版面/标题检测与语义分段各自应承担什么责任；如何处理重复页眉、跨页段落和重叠窗口。
2. 200+ 条已复核事实进入 LLM 时，如何采用局部分批、全局压缩/校准和可恢复执行，避免单次上下文过大或独立批次互相失明。

本文只引用官方文档、论文原文或项目官方源代码。外部证据与本项目工程推论分开标注；没有来源直接证明的项目参数均列为“待验证”，不写成硬约束。

## 一、结论摘要

### 结论 1：机械窗口不能保证“场景切分正确”

PDF 本质上保存的是页面上的绘制指令和位置，不必包含段落、标题、表格或页眉等语义层。pypdf 官方文档直接把页眉/页脚、段落、表格、阅读顺序和扫描件 OCR 列为文本提取中的歧义；因此只看每页首行、大小写或标点，无法证明这里就是章节、地点或场景边界。[S1]

机械层能够可靠承诺的是：**选定页段全部被覆盖、每条内容有稳定来源页、请求不会无限增大、跨边界内容能看到邻页上下文、失败可以定位并重试**。它不能可靠承诺：“一个窗口就是一个场景”或“下一页首行看起来像标题，所以语义在此结束”。

标题/版面检测仍然有价值，但只能输出可审计的候选信号。DocLayNet 把 `Page-header`、`Page-footer`、`Section-header` 和 `Title` 作为不同版面类别；其标注规则刻意以单页可见版面为准，不尝试判断正文的文学或领域语义。论文还显示 `Title` 的标注一致性明显弱于多数类别，说明即使在人类标注的版面任务中，“标题”也不是无歧义事实。[S2]

### 结论 2：重叠窗口解决的是边界召回，不是语义归属

官方 tokenizer API 把 `stride` 定义为：当输入溢出模型最大长度时，在相邻返回块之间保留指定数量的重叠 token；这是一种防止边界内容完全丢失的传输策略。[S6] Docling 的 `HierarchicalChunker` 则尽量遵循文档层级，`HybridChunker` 再按 tokenizer 限制拆分或合并，并把标题、caption 等元数据保留给下游 grounding。[S4]

据此，本项目现有的“非重叠负责页 core + 相邻上下文页 context”方向合理：core 决定候选归属，context 只补全跨界事实。需要修正的是 UI 和命名：`heading` 应表示“下一页疑似标题的审计信号”，不能显示成“标题分页”并暗示该窗口已按内容分段。

### 结论 3：284 条事实不应独立分批直接出最终卡片

长上下文模型即使能容纳全部输入，也不等于会稳定使用全部输入。“Lost in the Middle”在多文档问答和键值检索中发现：信息位于上下文开头或结尾时表现通常更好，位于中间时显著下降，且长上下文模型也可能低于显式上下文窗口所暗示的性能。[S8]

Summ^N 证明了多阶段切分、局部压缩、再递归汇总可以处理超过单次模型上限的长输入；但 2025 年 NAACL 的对照研究同时指出，中间摘要会因多阶段信息损失和缺少全局上下文而损害最终结果，混合使用压缩表示与原文检索通常更稳。[S7][S9]

因此推荐的不是简单 `map -> 直接合并最终卡`，而是：

`局部事实批次 -> 局部结构提议/索引 -> 全局压缩校准与卡片规划 -> 按规划回读相关原始事实 -> 最终卡片 -> 确定性校验`

全局阶段只读取紧凑索引来决定合并、拆分、关联和覆盖；最终卡片字段仍回读原始已提升事实，不把多轮摘要当作新的事实来源。

### 结论 4：失败恢复必须以“批次尝试”为单位保存，最终结果原子提交

MapReduce 原论文的容错做法是重新执行失败的 map/reduce 任务，并以原子重命名发布完成输出；论文还特别区分了确定性与非确定性 map/reduce 函数的语义。[S10] LLM 调用属于可能非确定性的外部计算，因此一次重试不应静默覆盖旧响应。

本项目应保存不可变输入快照、批次 ID/哈希、prompt/schema/model 版本、每次 attempt 的原始响应与校验错误。只重跑失败批次；所有局部结果有效后再运行全局校准；最终卡片通过确定性校验后一次性写入书架。中途中断时不得要求 GM 重做已经成功的批次，也不得把半套卡片当成正式草案。

## 二、PDF 机械处理的能力边界

### 2.1 三层职责应明确分离

| 层 | 应承担的责任 | 不应声称的能力 |
|---|---|---|
| 页面/版面层 | 文件版本、PDF 页码、是否有可抽取文字、OCR/低质量提示、文本块与坐标、阅读顺序候选、页眉/页脚/标题候选 | 不判断“这是一个完整场景”“本段故事在此结束” |
| 机械窗口层 | 全范围覆盖、token/请求预算、core 归属、邻页 context、进度、失败定位、幂等重试 | 不把窗口边界写回章节/场景 schema |
| 语义整理层 | 基于已抽取事实提出章节、地点、场景、人物、线索和时间线的聚类/关系候选，保留冲突和开放问题 | 不改变 GM 选定页段，不删除原文来源，不把模型分组直接提升为事实 |

这一分层与 Docling 的管线相符：其 PDF 转换先构造文本、图像和表格单元，再做版面聚类与分类、阅读顺序与元素组装，最后才得到统一文档结构；OCR、版面分类与文档组装是不同阶段。[S3] Docling 的标题层级分类也是可选功能，官方文档明确说明默认关闭，因为错误层级比缺少层级更糟；其启发式仅依据字号等外观推断相对层级。[S5]

**工程推论：**本项目的 `_looks_like_page_heading()` 可以保留为低成本提示，但不能成为语义边界判定器。尤其是“每页第一行、全大写、较短”这类规则，恰好也会命中重复页眉。

### 2.2 重复页眉/页脚应先于标题判定处理

pypdf 官方示例展示了按纵向坐标排除页眉/页脚区域，但同时警告复杂 PDF 中坐标可能难以解释。[S1] DocLayNet 则把页眉、页脚与标题分成独立类别。[S2] 早期的 page-association 研究还提出：页眉/页脚不只看单页位置，而应结合跨页重复关系进行识别。[S11]

**建议的可审计机械规则：**

1. 在同一源文件、相邻或同章节多数页面的顶部/底部区域，对规范化文本做重复频率统计。
2. 同时满足“位置稳定 + 文本重复/模式重复”的行，标记为 `running_header_candidate` 或 `running_footer_candidate`。
3. 从标题启发式的输入中排除这些候选，但保留原始文本、坐标、命中页和置信理由，便于审计。
4. 页码、章名加页码、左右页交替书名等模式需要规范化后匹配；不要只按字符串完全相等。
5. 对无法取得坐标或 OCR 顺序混乱的页面，只降低标题信号可信度，不删除文本。

第 1-4 条是结合上述来源作出的项目工程推论，不是某篇来源直接规定的唯一算法。是否采用、重复阈值和页面区域比例均需在《奈亚拉托提普的面具》真实页面上做对照后确定。

### 2.3 跨页段落应由覆盖与归属协议兜底

机械算法无法保证一个地点描述不会恰好横跨 `p7-p8`，而下一地点从 `p8-p9` 开始。可保证的是：

1. 每页恰有一个负责窗口 core，避免同一候选因 overlap 被重复提升。
2. 每个 core 携带前后邻接 context；跨页事实可引用多个来源页。
3. 归属锚点采用最早支持页或明确的稳定规则，而不是“模型觉得属于哪一窗”。
4. context 被截断时记录 `truncated_pages`，并允许对该页按 token 预算扩大/重跑。
5. 后续实体/场景聚类可以合并来自不同窗口的候选，但合并结果仍保留所有 fact/source IDs。

本项目现有 prompt 已采用“最早引用页必须位于 core”的归属约束，这符合上述目标。重叠的目的不是让两个窗口分别生成一套卡片，而是让边界事实至少在一个窗口中拥有足够上下文。

### 2.4 “标题分页”应改为审计语言

当前 `boundary_basis = heading | continuation | sentence_end | char_budget | page_limit | scope_end` 混合了两类信息：

- **真正决定切分的约束**：页数、输入预算、范围结束。
- **对边界附近文本的观察**：疑似标题、疑似续接、句末。

若 core 实际由三页上限或输入预算决定，却在 UI 只显示“标题分页”，会让 GM 误以为系统已理解章节结构。建议将数据与显示拆成：

- `cut_reason`: `page_budget | token_budget | scope_end`
- `boundary_signals`: `possible_heading | possible_continuation | sentence_end | repeated_header_suspected`
- UI 主文案：“机械窗口 p96-98”；次要审计信息：“下一页疑似标题（未确认）”。

这不是要求机械层增加更多“聪明切分”，而是要求它更诚实地表达能力边界。

### 2.5 如何验证机械切分，而不是验证“场景正确率”

机械层的自动验收应检查：

| 指标 | 验收含义 |
|---|---|
| core coverage | GM 选择的每一页被且只被一个 core 覆盖 |
| context continuity | 相邻 core 的边界页在至少一侧以 context 出现 |
| source preservation | 每条候选的来源均在该窗可见页内，跨页引用不会被压成单页 |
| truncation visibility | 被截断的页明确记录，可定向重跑 |
| retry isolation | 一个窗口失败不重跑已成功窗口，不改写已复核候选 |
| header false-positive rate | 真实样本中的重复页眉不会大量显示为“已确认标题” |

“场景边界是否正确”属于语义整理层，应以 GM 对聚类/章节建议的接受、拆分、合并和修改量衡量，不能拿机械窗口数代替。

## 三、200+ 事实的分层生成模式

### 3.1 当前独立分批方案的风险

当前实现按来源页排序，以约 `80,000` 字符为单批上限；每批直接生成 `DerivedCard`，之后仅按 `(type, title)` 和字段完全相同情况合并。对于 284 条事实，这种设计解决了“单请求过大”，但没有解决“全局组织”问题：

1. 同一 NPC、地点、线索链或时钟可能分布在不同批次；各批看不到另一批的事实。
2. 同一实体可能被不同批次起不同标题，精确标题合并无法识别。
3. 同名卡片字段不同会被并列保留，但没有全局阶段判断它们是冲突、阶段变化还是应合并内容。
4. 每批都被要求生成局部卡片，容易得到按传输块而不是按桌边用途组织的产物。
5. 失败后任务虽记录 `completed_batches`，但局部卡片只在进程内列表中累积；进程重启仍可能要求重做已完成调用。

上述是对本项目当前代码的工程审计，不是外部论文结论。

### 3.2 推荐管线：map -> global plan -> materialize -> validate

#### Stage 0：确定性输入快照与全局轻量索引

建立一个不可变 `ArtifactGenerationRun`：

- workspace、profile、session_minutes；
- 已提升事实的 ID、内容哈希、来源与版本；
- 模型、prompt/schema、tokenizer/预算配置；
- 运行 ID 和创建时间。

在调用 LLM 前，用确定性代码建立轻量索引：来源页序、事实类型、显式 tags、已有 possible links、规范化实体名候选。这里不自动发明关系，只用于把明显相邻或已有链接的事实尽量放进同批。

#### Stage 1：局部 map 批次

每批不直接宣告最终卡片，而返回 `LocalArtifactProposal`：

- 局部单元候选：地点/场景、人物、线索、威胁、时间线、结局等；
- 每个单元引用的 fact IDs；
- 实体名称与别名候选；
- 本批可确认的关系；
- 指向其他批次的待解析键或开放问题；
- 一个短小、结构化的 batch digest。

批次按实际 tokenizer 计算，而不是按字符数估算。Hugging Face 官方 tokenizer 明确以 token 长度执行截断、溢出和 stride；Docling `HybridChunker` 也把 tokenizer-aware splitting/merging 作为独立步骤。[S4][S6]

#### Stage 2：全局 reduce/校准

全局阶段读取所有局部 digest 和索引，不读取 284 条事实全文，输出：

- 全局实体/场景 ID 与别名对齐；
- 哪些局部单元应合并、拆分或保留为冲突；
- 线索到地点/NPC/后果的连接；
- 时间线、威胁和结局的跨批关系；
- 目标板块需要的最终卡片清单；
- 每张卡需要回读的 fact IDs；
- 未覆盖事实和开放问题。

如果所有 digest 仍超出 reducer 预算，按 Summ^N 的多阶段思想递归生成区域级摘要，再进入最终 reduce；每级都必须保留子节点 ID 和 fact ID 并集，不能只留自由文本摘要。[S7]

#### Stage 3：按卡回读原始事实并生成最终字段

对全局计划中的每张卡，只取它引用的已提升原始事实，加上必要的邻接/关系事实，生成最终卡片字段。这样既保留全局组织，又避免用经过多轮压缩的摘要承担事实依据。

这一“压缩用于规划、原事实用于落卡”的混合策略是针对 NAACL 2025 结果的工程回应：纯中间摘要可减少长度，但会产生信息损失和全局上下文缺失；混合方法能兼顾效率与原始证据。[S9]

#### Stage 4：确定性校验与原子提交

至少校验：

- 所有 `fact_ids` 存在、已提升且属于输入快照；
- 每个字段的 `field_sources` 是该卡 `fact_ids` 的非空子集；
- 全局计划中的卡均已生成，无未知卡型；
- 重复标题/实体、冲突字段和未解析跨批关系有显式状态；
- 运行板块具备必要锚点，但不为了满足数量而发明内容；
- coverage 报告列出未使用事实及原因，而不是默认要求每条事实都进入卡片。

全部通过后一次性写入书架。任何阶段失败都保留中间产物供重试和审计，书架继续显示上一份已批准状态。

### 3.3 上下文预算必须以模型能力和 token 为准

字符数只能作为未知 tokenizer 时的保守退路，不能作为跨模型稳定契约。建议预算公式为：

`可用事实 token = 模型上下文上限 - 系统/契约 token - 预计输出 token - 安全余量`

其中模型上下文上限与最大输出必须来自当前上游配置或能力探测；系统 prompt、profile schema、JSON 包装和修复轮次都占用预算。安全余量比例是本项目待验证参数，不应先写死为通用真理。

每批应在提交前记录：估算输入 token、保留输出 token、实际 usage（若上游返回）、是否截断。若上游不提供 tokenizer，应选明确的保守估算器，并把估算方法写入 job 版本；不要静默继续使用 `80,000 chars` 作为所有模型的共同上限。

### 3.4 批次组织：局部连续性优先，有限桥接而非全量重复

推荐排序优先级：

1. 同一显式来源页段和相邻页；
2. 已有相同实体/tag/link 的事实；
3. 同类但距离较远的事实只通过全局索引关联，不为了“可能相关”复制全文；
4. 跨批桥接只携带少量关联事实 ID、短摘要和邻接事实，避免 overlap 造成大量重复卡片。

任何事实可出现在多个批次的 context，但只能有一个 owner batch。owner 规则应确定性保存，局部 proposal 必须区分 owned facts 与 context facts。这与抽取阶段 core/context 的思想一致。

### 3.5 失败恢复与非确定性

建议状态层级：

`generation_run -> map_batch -> reduce_pass -> card_materialization -> validation -> commit`

每个子任务保存：

- `status = queued | running | succeeded | failed | superseded`
- input hash、依赖输出 hash、attempt number；
- 原始响应、规范化响应、校验错误、耗时、usage；
- prompt/schema/model/upstream 版本。

恢复规则：

1. map 某批失败，只重试该批；成功批输出持久化。
2. map 输出有变化时，使依赖它的 reduce/materialization 标为 `superseded`，不删除旧记录。
3. reduce 失败，只重试 reduce；不重跑 map。
4. 单张卡 materialization 失败，只重试该卡；其它卡保持候选完成状态。
5. 重试产生新 attempt；由于 LLM 可能非确定，旧响应不能被静默覆盖。
6. 最终 commit 使用输入快照/工作区版本检查，避免生成期间人工编辑被覆盖。

MapReduce 原论文中的失败任务重执行、完成输出原子发布和确定性语义讨论为这套恢复模型提供了基础，但 LLM 非确定性要求本项目比经典 MapReduce 多保存 attempt 历史和人工可见 diff。[S10]

## 四、对本项目四个具体问题的证据化判断

| 用户观察 | 判断 | 推荐处理 |
|---|---|---|
| 200+ 候选批量接受出现 `Error: [object Object]` | 与 LLM 上下文无关；已知是前端把 FastAPI/Pydantic 对象直接转字符串的问题 | 保留结构化错误格式化与客户端分批；服务端限制应显示具体条数/字段，不把它误诊为总量上限 |
| 机械分页几乎全是“标题分页” | 不是正常的语义分段证明；重复页眉和弱标题启发式可造成高假阳性 | 分离 `cut_reason` 与 `boundary_signals`，做跨页页眉候选排除；UI 标记“疑似” |
| 284 条事实无法一次生成 | 限制单次上下文是合理保护，但把拆分工作交给 GM 不符合产品目标 | 系统内部执行分层 map/global-plan/materialize；GM 仍操作一个“第一章”工作区 |
| 直接分批后合并卡片 | 能绕过单请求大小，但缺少全局实体、线索、时间线和冲突校准 | 作为临时工程实验保留，不应视为奈面章节级可用方案 |

## 五、建议的决策文本

若项目负责人确认，可在下一份重评中采用以下表述：

1. **机械切分决策**：机械层只保证范围覆盖、token 预算、稳定归属、邻页上下文、来源和可恢复执行；标题/续接/句末均为边界信号，不是章节或场景事实。语义场景边界由后续 LLM 候选与 GM 复核产生。
2. **工作区决策**：重新分析同一章节应保留一个书架项目，在项目内保存版本化 analysis/generation runs；机械窗口和批次不成为平行书架。
3. **大事实集决策**：章节级产物采用“局部 proposal + 全局校准/规划 + 回读原始事实落卡 + 确定性校验”的分层管线；不要求 GM 手工把第一章拆成多个备团项目。
4. **恢复决策**：成功子任务持久化，按失败子任务重试；每次 LLM 重试保留 attempt，最终结果原子提交且不覆盖 GM 编辑。
5. **验收决策**：用奈面第一章建立人工核对集，分别测窗口覆盖、页眉误报、跨页事实召回、跨批关系保留、产物重复/冲突、GM 修改量和失败恢复；不以“请求成功”或“生成卡片数量”作为章节级验收。

## 六、待验证问题

这些问题没有足够一手证据给出本项目唯一参数，需要真实样本实验：

1. 重复页眉判定的页数阈值、顶部/底部区域比例和左右页交替规则。
2. 抽取窗口 core 页数、context token 数和密集页的自适应策略。
3. 新上游真实模型的上下文、最大输出、tokenizer、限流与 usage 返回能力。
4. map 批次按页邻近、实体关联或事实类型组织时，哪一种最少产生漏链和重复卡。
5. 全局 digest 的最小字段集，以及何时需要第二层递归 reduce。
6. 章节级最终产物是否需要一次全局语言/命名润色；若需要，该步骤只能改表达，不能改变事实关系与来源。

## 七、一手来源

- **[S1] pypdf 官方文档，Extract Text from a PDF**：说明 PDF 没有可靠语义层，列出页眉/页脚、段落、表格、阅读顺序和扫描 OCR 等歧义，并给出按坐标过滤页眉/页脚的 visitor 示例。  
  https://pypdf.readthedocs.io/en/stable/user/extract-text.html
- **[S2] Pfitzmann et al., DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis, KDD 2022 / arXiv 原文**：定义 `Page-header`、`Page-footer`、`Section-header`、`Title` 等 11 类版面标签；强调单页可见布局而非领域语义，并报告标签一致性。  
  https://arxiv.org/abs/2206.01062
- **[S3] Docling Technical Report, arXiv 原文**：描述 PDF backend、OCR、版面分析、阅读顺序和文档元素组装的分阶段管线，以及统一文档模型中的 provenance。  
  https://arxiv.org/abs/2408.09869
- **[S4] Docling 官方文档，Chunking**：`HierarchicalChunker` 遵循文档层级并保留标题/caption 等元数据；`HybridChunker` 增加 tokenizer-aware splitting and merging。  
  https://docling-project.github.io/docling/concepts/chunking/
- **[S5] Docling 官方文档，Heading classification**：说明标题层级分类默认关闭，因为错误层级可能比没有层级更糟；内置启发式依据字体大小等外观推断相对层级。  
  https://docling-project.github.io/docling/usage/heading_levels/
- **[S6] Hugging Face Transformers 官方文档，Tokenizer API**：定义 truncation、`return_overflowing_tokens` 与 `stride`，其中 stride 在溢出块间返回重叠 token。  
  https://huggingface.co/docs/transformers/en/main_classes/tokenizer
- **[S7] Zhang et al., Summ^N: A Multi-Stage Summarization Framework for Long Input Dialogues and Documents, ACL 2022 原文**：通过多阶段 split-then-summarize 处理任意长输入，并将中间摘要递归送入后续阶段。  
  https://aclanthology.org/2022.acl-long.112/
- **[S8] Liu et al., Lost in the Middle: How Language Models Use Long Contexts, TACL 2024 原文**：长上下文性能对信息位置敏感，相关信息位于中间时常显著下降。  
  https://aclanthology.org/2024.tacl-1.9/
- **[S9] Pratapa and Mitamura, Scaling Multi-Document Event Summarization: Evaluating Compression vs. Full-Text Approaches, NAACL 2025 原文**：比较全文与中间压缩，指出多阶段信息损失和缺少全局上下文，并建议探索结合选择性压缩与长上下文模型的混合方案。  
  https://aclanthology.org/2025.naacl-short.44/
- **[S10] Dean and Ghemawat, MapReduce: Simplified Data Processing on Large Clusters, OSDI 2004 原文**：描述 map/reduce 中间结果、失败任务重执行、原子发布和确定性/非确定性任务的语义差异。  
  https://www.usenix.org/legacy/event/osdi04/tech/full_papers/dean/dean.pdf
- **[S11] Xiaofan Lin, Header and Footer Extraction by Page-Association, SPIE 2003 原文/DOI 页面**：使用页面之间的关联以及内容、位置特征识别页眉页脚，支持“不应只看单页首行”的方向。  
  https://doi.org/10.1117/12.472833
