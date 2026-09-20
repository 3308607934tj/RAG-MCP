# 项目介绍：Modular RAG MCP Server

> 一份"从零讲清楚这个项目**是什么、需要什么、能做什么**"的说明。
> 面向第一次接触本项目的人，也适合需要把它讲给别人听的人。
>
> 相关文档：[README](../README.md) · [开发规格 DEV_SPEC.md](../DEV_SPEC.md) · [复刻排错记录](REPRODUCTION_NOTES.md)

## 目录

- [一、一句话定位](#一一句话定位)
- [二、它解决什么问题](#二它解决什么问题)
- [三、两个核心概念](#三两个核心概念)
- [四、能做什么（六大模块）](#四能做什么六大模块)
- [五、需要什么（依赖与前提）](#五需要什么依赖与前提)
- [六、两条核心链路](#六两条核心链路)
- [七、架构特点](#七架构特点)
- [八、如何输入你自己的文档](#八如何输入你自己的文档)
- [九、边界与已知限制](#九边界与已知限制)
- [十、适合谁用](#十适合谁用)
- [十一、本仓库的复刻验证状态](#十一本仓库的复刻验证状态)
- [附：常用命令速查](#附常用命令速查)

---

## 一、一句话定位

**把"你的文档 → 一个 AI 能直接查的知识库"这条链路做成一个模块化、可插拔、可观测的服务，再通过 MCP 协议把这个知识库暴露给 Claude / Copilot 等 AI 助手。**

两个关键词：

| 关键词 | 含义 |
|---|---|
| **RAG**（Retrieval-Augmented Generation，检索增强生成） | 让大模型能回答"你自己的文档里写了什么" |
| **MCP**（Model Context Protocol） | 让 AI 助手能**自动调用**这个知识库，而不是你手动复制粘贴 |

---

## 二、它解决什么问题

大模型不知道你的私有文档。传统三条路各有代价：

| 方案 | 问题 |
|---|---|
| 把文档全塞进上下文 | 贵、有长度上限、每次都要重传 |
| 微调模型 | 贵、慢、文档一改就失效 |
| **RAG：把文档存起来，按需检索相关片段喂给模型** | ← 本项目走的路 |

而 MCP 解决的是**第二层问题**：检索能力做出来了，怎么让 AI 助手用上？
答案是把它包装成 **MCP 工具** —— 之后你在 Claude Code / Copilot 里用自然语言提问，助手会**自己判断并调用**你的知识库。

---

## 三、两个核心概念

### RAG 的三步

```
离线：文档 → 切分成片段 → 转成向量 → 存进向量库
在线：提问 → 转成向量 → 检索最相似的片段 → 连同问题一起喂给大模型
```

关键点：**大模型看到的不是你的全部文档，而是检索出来的几个最相关片段**。所以检索质量直接决定回答质量。

### MCP 是什么

一个开放协议，规定"AI 助手如何调用外部工具"。本项目作为 **MCP Server** 运行，把三个检索能力注册成工具；Claude Code / Copilot 作为 **MCP Client**，在需要时调用它们。

传输方式：**stdio**（标准输入输出上的 JSON-RPC），意味着服务必须运行在**能访问你数据的那台机器上**。

---

## 四、能做什么（六大模块）

### 4.1 数据摄取（Ingestion）

**输入**：PDF / Markdown / TXT / 代码文件
**输出**：向量库 + 关键词索引 + 图片文件

管线共 **9 个阶段**：

| # | 阶段 | 做什么 |
|---|---|---|
| ① | integrity | SHA256 文件指纹 + 查历史库，判断是否跳过 |
| ② | quality_check | **仅 PDF**：抽样前几页检测文本质量，太差则拒收 |
| ③ | load | PDF 走 MarkItDown 转 Markdown + pypdf 抽图；其余走 TextLoader |
| ④ | split | 递归字符切分（默认 1000 字符 / 重叠 200） |
| ⑤ | transform | 精炼 → 元数据增强（标题/摘要/标签）→ 图片描述注入 |
| ⑥ | encode | Dense 向量（默认 1024 维）+ Sparse 稀疏编码 |
| ⑦ | store | 先写向量库，再建 BM25 倒排索引（两边共用同一 chunk_id） |
| ⑧ | image_store | 图片落盘 + SQLite 索引 |
| ⑨ | finalize | 标记成功、写 trace |

**四层幂等**：文件级（SHA256 跳过）、向量级（同 id 覆盖）、图片级（主键冲突更新）、BM25 级（增量替换）。
**重复上传同一文件不会产生脏数据。**

> 注：②（质量门禁）和 ⑧（图片存储）在部分文档中未提及，实际代码中存在。详见 [复刻排错记录](REPRODUCTION_NOTES.md)。

### 4.2 混合检索（Hybrid Search）

单独用向量检索会漏掉**专有名词的精确匹配**（比如内部代号），单独用关键词检索又抓不住**同义改写**。所以两条路一起走：

```
查询
 ├─→ Dense 向量检索（语义相似）──┐
 └─→ Sparse BM25（关键词精确）──┤
                                ├─→ RRF 融合（按排名倒数求和）
                                └─→ 重排（LLM 或 CrossEncoder，可关闭）
                                     └─→ 带引用的结果
```

**RRF（Reciprocal Rank Fusion）**：对每条结果，按它在各路结果中的**排名**算 `1/(k + rank)` 再相加（默认 `k=60`）。
注意它是**排名**驱动的，分数量级与余弦相似度不可比 —— 别用绝对分值判断检索质量。

### 4.3 MCP Server —— 对外的三个工具

这是**最终交付形态**，AI 助手看到的就是这三个工具：

| 工具 | 参数 | 返回 |
|---|---|---|
| `query_knowledge_hub` | `query`（必填）、`top_k`（1–20）、`collection` | 检索片段 + 结构化引用（`source` / `chunk_id` / `score`） |
| `list_collections` | 无 | 所有集合名 |
| `get_document_summary` | `doc_id`（必填）、`collection` | 标题 / 摘要 / 标签 |

> ⚠️ **重要认知**：`query_knowledge_hub` 返回的是**检索片段和引用**，**不是生成好的答案**。
> 答案由客户端的大模型基于这些片段组织。**它本质是检索工具，不是问答机器人。**

### 4.4 Dashboard（Streamlit 六页面）

| 页面 | 内容 |
|---|---|
| System Overview | 各组件配置卡片 + 数据量统计 |
| Data Browser | 浏览已入库文档与 Chunk 详情 |
| Ingestion Manager | 上传文件触发摄取，实时进度 |
| Ingestion Traces | 摄取历史 + 阶段耗时瀑布图 |
| Query Traces | 查询历史 + 重排前后排名变化 |
| Evaluation | 运行评估、查看历史报告 |

### 4.5 可观测性

摄取链路与查询链路**每个阶段都打点**，写入 `logs/traces.jsonl`，Dashboard 可视化。

> 已知缺口：命令行摄取不写 trace，MCP 调用也未打点。详见复刻排错记录第 05 条。

### 4.6 评估体系（Ragas / Custom）

支持 hit_rate / MRR / faithfulness 等指标，但**自带的是占位模板**（`tests/fixtures/golden_test_set.json` 引用了仓库中不存在的 PDF）。
要真正用于回归，需要**按你自己的语料重写** golden set。

---

## 五、需要什么（依赖与前提）

| 类别 | 必需性 | 说明 |
|---|---|---|
| **Python** | 必需 | 3.10+，推荐 **3.11**（3.13 对 torch/onnxruntime 生态风险高） |
| **依赖包** | 必需 | `chromadb`、`mcp`、`openai`、`streamlit`、`fastapi`、`markitdown`、`pypdf`，以及**重量级的 `sentence-transformers`（连带 torch，约 2–3 GB）** |
| **Embedding 服务** | 🔴 **硬依赖** | 默认调用云端 API（示例配置为阿里云百炼 `text-embedding-v3`，1024 维）。**没有它连摄取都做不了** |
| **LLM 服务** | 🟡 软依赖 | 用于**重排**和**图片描述**；两者都可关闭 |
| **配置文件** | 必需 | `config/settings.yaml`，含 `llm` / `embedding` / `vision_llm` 三段密钥 |
| **你自己的文档** | 必需 | 知识库的内容来源 |
| **网络** | 必需 | 需能访问 LLM / Embedding 的 API |

**一句话总结**：一个 Python 环境 + **一个 Embedding API key（必需）** + 一个 LLM API key（可选）+ 你的文档。

### 配置要点

```yaml
llm:          # 生成与重排用的模型
  provider: "deepseek"
  model: "deepseek-flash"
  api_key: "你的真实密钥"       # ← 必须写字面值！本项目不支持 ${VAR} 展开
embedding:    # 检索用的向量模型（硬依赖）
  provider: "qwen"
  model: "text-embedding-v3"
  dimensions: 1024
vector_store:
  persist_directory: "./data/db/chroma"
  collection_name: "knowledge_hub"
retrieval:
  dense_top_k: 20
  sparse_top_k: 20
  fusion_top_k: 10
  rrf_k: 60
rerank:
  enabled: true
  provider: "llm"             # llm / cross_encoder / none
  top_k: 5
ingestion:
  chunk_size: 1000
  chunk_overlap: 200
  splitter: "recursive"
```

> ⚠️ 文档中声称的"支持 `${VAR_NAME}` 环境变量"**在代码中并未实现**，密钥必须写成字面值。详见复刻排错记录第 03 条。

---

## 六、两条核心链路

```
【摄取链路】（离线，跑一次）
文档 → 指纹校验 → 质量门禁 → 解析 → 切分 → 转换 → 向量化
     → 写向量库 + 建关键词索引 + 存图片 → 记录成功

【查询链路】（在线，每次提问）
提问 → 查询预处理 → Dense + Sparse 双路召回 → RRF 融合
     → 重排 → 上下文扩展 → 返回片段 + 引用 → 客户端 LLM 组织答案
```

---

## 七、架构特点

### 三层分工

| 层 | 职责 |
|---|---|
| `src/core/` | 契约与编排：数据类型、配置加载、查询引擎、响应构建 |
| `src/libs/` | 适配层：LLM / Embedding / Reranker / VectorStore / Loader / Splitter 的抽象接口与多实现 |
| `src/ingestion/` | 摄取编排：管线、切分、转换、存储 |

依赖方向：`libs` 可以依赖 `core.types`（数据契约），但**不依赖 `core.settings`**；`ingestion` 依赖两者。

### 五大工厂：换后端只改配置

```yaml
llm:       { provider: "deepseek" }   # openai / azure / ollama / qwen / gemini
embedding: { provider: "qwen" }       # openai / azure / ollama / local
rerank:    { provider: "llm" }        # llm / cross_encoder / none
vector_store: { provider: "chroma" }
evaluation:   { provider: "custom" }  # custom / ragas
```

**零代码修改即可切换实现** —— 这是本项目最核心的设计目标。

> 一个诚实的补充：工厂模式解决了"运行时选择"，但 `__init__.py` 的 eager import 会让所有 provider 的依赖都变成强制的（例如只用一个云端 embedding，也要装 torch）。详见复刻排错记录第 12 条。

---

## 八、如何输入你自己的文档

### 8.1 支持的格式

loader 选择只看一件事（`src/ingestion/pipeline.py:258-263`）：

```python
if suffix == ".pdf":  return self._loader    # PdfLoader：MarkItDown + pypdf
return TextLoader()                          # 其它一律按纯文本读
```

| 类型 | 支持 | 说明 |
|---|---|---|
| **PDF** | ✅ | 走专用 loader，MarkItDown 转 Markdown + pypdf 抽图，效果最好 |
| **纯文本类**（40 种） | ✅ | 见下方清单 |
| `.docx` / `.pptx` / `.xlsx` / `.rtf` / `.odt` / `.epub` | ❌ | 会被当纯文本读 → **乱码入库** |
| `.csv` / `.log` / `.rst` / `.tex` / `.ipynb` | ❌ | 不在清单中 |

**支持的纯文本扩展名**（`src/libs/loader/text_loader.py:17-24`）：

```
代码：.py .js .ts .java .go .rs .rb .php .swift .kt .scala .c .cpp .h .hpp .sh .bash
文档：.md .txt .xml .html .json .yaml .yml .toml .ini .cfg .conf
前端：.css .scss .less .vue .svelte .jsx .tsx
其它：.sql .env .dockerfile .gitignore .editorconfig ...
```

**格式转换建议**：

| 原始文件 | 怎么转 |
|---|---|
| Word / PPT | 另存为 **PDF**（推荐） |
| Excel | 复制成 `.md` 表格，或导出后改 `.txt` |
| 网页 | 存成 `.html`，或复制正文存 `.md` |
| 扫描版 PDF | ⚠️ 只有图像没有文字层，会被**质量门禁拒收** |

### 8.2 ⚠️ 编码陷阱（中文用户必读）

`TextLoader` 以 `utf-8` + `errors="ignore"` 读取：

```python
fp.read_text(encoding="utf-8", errors="ignore")
```

**非 UTF-8 编码（如 Windows 记事本默认的 ANSI/GBK）的中文文本会被静默丢字符**，且**不报错**。

👉 保存文档时务必选 **UTF-8**（VS Code：右下角编码 → Save with Encoding → UTF-8）。

### 8.3 三种输入方式

**🅰 CLI（推荐，最可控）**

```bash
cd <仓库根目录>

# 多种格式，递归整个目录
python scripts/ingest_files.py --path "D:\我的文档"

# 只摄特定类型
python scripts/ingest_files.py --path "D:\代码库" --extensions .py .md

# 单个 PDF 或 PDF 目录
python scripts/ingest.py --path "D:\论文"

# 存到指定集合（默认 knowledge_hub）
python scripts/ingest_files.py --path "D:\工作资料" --collection work

# 强制重新摄取（默认按 SHA256 跳过已摄过的）
python scripts/ingest_files.py --path "D:\我的文档" --force
```

**🅱 Dashboard 网页上传**

```bash
python scripts/start_dashboard.py     # → http://localhost:8501
```

进入 **Ingestion Manager** 页面上传，有实时进度条。
顺带好处：**网页端摄取会写 trace**，能在 Ingestion Traces 页看到历史。

**🅲 代码调用（自动化）**

```python
from core.settings import load_settings
from ingestion.pipeline import IngestionPipeline

settings = load_settings()
result = IngestionPipeline(settings).run("D:/doc/a.pdf", collection="work", force=False)
```

### 8.4 路径写法（Windows 常见坑）

| 坑 | 正确做法 |
|---|---|
| 路径含空格或中文 | **必须加引号**：`--path "D:\我的 文档"` |
| 相对路径的基准 | 是**当前工作目录**，不是仓库根 → 先 `cd` 到仓库根 |
| 含 `.git` / `node_modules` 的目录 | **别直接摄**，会灌进大量垃圾文件 |

### 8.5 耗时与费用

- 每个 chunk 都要调用一次 **embedding API**（批量），是**联网 + 计费**操作
- 100 页 PDF → 约几十到上百个 chunk → 几十秒到几分钟
- **建议先用 3~5 个文件试水**，确认效果再批量

### 8.6 摄取后验证

```bash
# 命令行验证（可看到 Dense/Sparse/RRF/重排全过程）
python scripts/query.py --query "文档里的一个独特关键词" --verbose
```

也可以用 Dashboard 的 **Data Browser** 查看入库的 chunk 与 metadata。

### 8.7 注意事项汇总

| # | 事项 |
|---|---|
| 1 | 重复摄取会被 SHA256 去重跳过；要重摄加 `--force` |
| 2 | **改 embedding 模型或维度后必须重建库**，否则新旧向量维度不一致 |
| 3 | 命令行摄取不写 trace（Dashboard 追踪页看不到） |
| 4 | 非 UTF-8 中文文本会静默丢字 |
| 5 | `collection` 是逻辑分组，查询和 MCP 调用都可指定 |

---

## 九、边界与已知限制

| 边界 | 说明 |
|---|---|
| **不是问答服务** | 只负责"找到相关片段"，生成答案的是客户端 LLM |
| **强依赖外部 Embedding 服务** | 默认配置下没有 API key 完全跑不起来 |
| **单机嵌入式向量库** | 向量库为嵌入式设计，不适合海量数据或分布式部署 |
| **多进程一致性有限** | 长驻进程可能看不到其他进程的新写入，摄取后建议重启服务 |
| **评估需自备数据** | 自带 golden set 是占位模板 |
| **相对路径依赖工作目录** | 配置中的相对路径按进程 cwd 解析，多入口场景下需注意 |
| **测试会写入 `test` 集合** | 部分测试 fixture 使用相对路径指向真实数据目录 |

上述多数问题在 [复刻排错记录](REPRODUCTION_NOTES.md) 中有完整的现象、定位过程与根因分析。

---

## 十、适合谁用

| 场景 | 是否合适 |
|---|---|
| 个人 / 团队文档库，接进 AI 编程助手 | ✅ 正合适 |
| 学习 RAG 的工程化实现（分层、工厂、幂等、可观测） | ✅ **这是它最大的价值** —— 一套完整的可运行参考实现 |
| 生产级大规模知识库（百万文档、多租户） | ❌ 需要更换向量库与架构 |
| 想要开箱即用的问答机器人 | ❌ 它只提供检索层，问答需要自己接 |

---

## 十一、本仓库的复刻验证状态

本仓库是该项目的**学习复刻**，以下环节均已实际跑通并记录：

| 环节 | 状态 |
|---|---|
| 环境 | Windows 11 + conda（Python 3.11） |
| 配置 | DeepSeek `deepseek-flash` + DashScope `text-embedding-v3`（1024 维） |
| 摄取链路 | 6 篇项目文档成功入库 |
| 检索链路 | Dense / Sparse / RRF / **LLM 重排** 逐段实测 |
| MCP Server | 三个工具在命令行与 Claude Code 桌面端均验证通过 |
| Dashboard | 六页面可正常启动 |
| 单元测试 | **341 passed / 1 skipped** |
| 排错记录 | 12 条真实问题，见 [REPRODUCTION_NOTES.md](REPRODUCTION_NOTES.md) |

---

## 附：常用命令速查

```bash
# ── 环境 ──────────────────────────────────────────
conda activate rag-mcp
pip install -e ".[dev,pdf-images]"      # 首次安装

# ── 摄取 ──────────────────────────────────────────
python scripts/ingest_files.py --path "目录" --extensions .md .pdf
python scripts/ingest.py --path "PDF 目录"
python scripts/ingest_files.py --path "目录" --collection work --force

# ── 检索验证 ──────────────────────────────────────
python scripts/query.py --query "问题" --verbose
python scripts/query.py --query "问题" --no-rerank     # 跳过重排
python scripts/query.py --query "问题" --collection work

# ── MCP ───────────────────────────────────────────
python -m mcp_server                     # 启动 MCP 服务器（stdio）
python scripts/test_mcp_list.py          # 三个工具冒烟测试

# ── 可视化 ────────────────────────────────────────
python scripts/start_dashboard.py        # → http://localhost:8501

# ── 测试与评估 ────────────────────────────────────
pytest tests/unit -q
python scripts/evaluate.py               # 需要自备 golden set

# ── 诊断 ──────────────────────────────────────────
python main.py                           # 仅加载配置，验证配置是否正确
```
