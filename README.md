# Modular RAG MCP Server

> 一个可插拔、可观测的模块化 RAG（检索增强生成）服务框架，通过 MCP（Model Context Protocol）协议对外暴露工具接口，支持 Copilot / Claude 等 AI 助手直接调用

---

## ⚠️ 关于本仓库

本仓库是 [kidoom/RAG-MCP](https://github.com/kidoom/RAG-MCP) 的**学习复刻**（基线提交 `8194ca2`），用于个人学习与工程实践记录。上游仓库未声明开源许可证，本仓库不用于分发或商业用途。

复刻过程中完整搭建了运行环境并跑通全链路：环境隔离 → 配置加载 → 数据摄取 → 混合检索（Dense + BM25 + RRF 融合）→ LLM 重排 → MCP 三工具 → Dashboard → AI 客户端接入。

### 我的改动

| # | 类型 | 内容 | 涉及文件 |
|---|------|------|----------|
| 1 | **缺陷修复** | `get_document_summary` 的 `tags` **编解码不对称** —— 写侧 `ChromaStore._sanitize_metadata()` 把 list 序列化为 JSON 字符串（Chroma 的 metadata 只接受标量类型），读侧却未做反向解析，导致 MCP 客户端收到 `["[\"code\", \"mcp\", ...]"]` 这样的畸形单元素数组 | `src/mcp_server/tools/get_document_summary.py` |
| 2 | **可移植性** | MCP 冒烟测试脚本硬编码了作者机器的绝对路径，他人 clone 后无法运行；改为从脚本自身位置推导项目根 + 使用 `sys.executable` | `scripts/test_mcp_list.py`、`scripts/test_mcp_encoding.py` |
| 3 | **测试补全** | 三个 MCP 工具此前只覆盖 1 个；补齐 `query_knowledge_hub` → 取 `chunk_id` → `get_document_summary` 的链式验证 | `scripts/test_mcp_list.py` |
| 4 | **缺陷定位** | `persist_directory` 相对路径依赖进程工作目录，MCP 客户端以其他目录启动时会**静默查到空库**（无任何报错） | 见排错记录第 07 条 |

### 复刻排错记录

[docs/REPRODUCTION_NOTES.md](docs/REPRODUCTION_NOTES.md) 记录了 **10 个真实问题**，每条按
**现象 → 定位过程 → 根因 → 处理 → 可迁移的知识点**组织，保留报错原文与代码行号，覆盖：

TLS 后端与代理配置 · Windows 环境隔离 · 文档与实现不符 · 可观测性覆盖缺口 ·
序列化对称性 · 进程工作目录陷阱 · RRF 算法性质 · MCP 客户端接入 · 排错方法论

---

## 📖 目录

- [关于本仓库](#-关于本仓库)
- [复刻排错记录](docs/REPRODUCTION_NOTES.md)
- [项目概述](#-项目概述)
- [快速开始](#-快速开始)
  - [项目架构可视化](#7-项目架构可视化)
---

## 🏗️ 项目概述

### 这个项目是什么

本项目将 RAG 最常见的核心环节——**检索（Hybrid Search + Rerank）**、**多模态视觉处理（Image Captioning）**、**RAG 评估（Ragas + Custom）**、**生成（LLM Response）**——以及当下热门的应用协议 **MCP（Model Context Protocol）** 串联为一个完整的、可运行的工程项目。

**项目的一大亮点是极易适配到你自己的业务中**。得益于全链路可插拔架构，你可以快速将它结合到自己已有的项目里，无论你的背景和需求如何，都能找到适合自己的使用方式。具体的使用策略会在后文 [谁适合用这个项目 & 怎么用](#-谁适合用这个项目--怎么用) 中详细展开。

### 不只是项目，更是一整套思路

**比这个项目本身更有价值的，是它背后蕴含的一整套工程化思路**：

- 如何编写 **DEV_SPEC**（开发规格文档）来驱动开发
- 如何用 **Skill** 基于 Spec 自动完成代码编写
- 如何用 **Skill** 进行自动化测试、打包、环境配置
- 如何基于可插拔架构进行扩展（比如扩展到 Agent）

**学会了思路，你可以自己做全新的项目和扩展**。以上每一步的具体做法、设计思路，在笔记中都有对应的视频讲解，建议配合观看。

### 核心能力一览

| 模块 | 能力 | 说明 |
|------|------|------|
| **Ingestion Pipeline** | PDF → Markdown → Chunk → Transform → Embedding → Upsert | 全链路数据摄取，支持多模态图片描述（Image Captioning） |
| **Hybrid Search** | Dense (向量) + Sparse (BM25) + RRF Fusion + Rerank | 粗排召回 + 精排重排的两段式检索架构 |
| **MCP Server** | 标准 MCP 协议暴露 Tools | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| **Dashboard** | Streamlit 六页面管理平台 | 系统总览 / 数据浏览 / Ingestion 管理 / 摄取追踪 / 查询追踪 / 评估面板 |
| **Evaluation** | Ragas + Custom 评估体系 | 支持 golden test set 回归测试，拒绝"凭感觉"调优 |
| **Observability** | 全链路白盒化追踪 | Ingestion 与 Query 两条链路的每一个中间状态透明可见 |
| **Skill 驱动全流程** | 从编写到测试、打包、配置一键完成 | auto-coder / qa-tester / package / setup 等 Skill 覆盖完整开发生命周期（笔记中每个 Skill 的使用和设计思路均有讲解，请参考配套视频） |

### 技术亮点

**🔌 全链路可插拔架构**：LLM / Embedding / Reranker / Splitter / VectorStore / Evaluator 每一个核心环节均定义了抽象接口，支持"乐高积木式"替换，通过配置文件一键切换后端，零代码修改。

**🔍 混合检索 + 重排**：BM25 稀疏检索解决专有名词精确匹配 + Dense Embedding 解决同义词语义匹配，RRF 融合后可选 Cross-Encoder / LLM Rerank 精排，平衡查全率与查准率。

**🖼️ 多模态图像处理**：采用 Image-to-Text 策略，利用 Vision LLM 自动生成图片描述并缝合进 Chunk，复用纯文本 RAG 链路即可实现"搜文字出图"。

**📡 MCP 生态集成**：遵循 Model Context Protocol 标准，可直接对接 GitHub Copilot、Claude Desktop 等 MCP Client，零前端开发，一次开发处处可用。

**📊 可视化管理 + 自动化评估**：Streamlit Dashboard 提供完整的数据管理与链路追踪能力，集成 Ragas 等评估框架，建立基于数据的迭代反馈回路。

**🧪 三层测试体系**：Unit / Integration / E2E 分层测试，覆盖独立模块逻辑、模块间交互、完整链路（MCP Client / Dashboard）。

**🤖 Skill 驱动全流程**：内置 auto-coder（自动编码）、qa-tester（自动测试）、package（清理打包）、setup（一键配置）等 Agent Skill，覆盖从代码编写到测试、打包、部署的完整开发生命周期。每个 Skill 的使用方法和设计思路在笔记的项目部分均有讲解视频，可参考学习。

> 📖 详细架构设计、模块说明和任务排期请参阅 [DEV_SPEC.md](DEV_SPEC.md)

---


## 🚀 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd Modular-RAG-MCP-Server
```

### 2. 一键配置（Setup Skill）

本项目提供了 **Setup Skill** 一键完成所有环境配置，包括：Provider 选择 → API Key 配置 → 依赖安装 → 配置文件生成 → Dashboard 启动。

在 VS Code 中打开项目，通过 Copilot / Claude 对话框输入：

```
setup
```

Agent 会自动引导你完成全部配置流程。

### 3. 手动配置与环境搭建

如果你不想使用 Setup Skill，也可以手动完成所有步骤：

#### 3.1 安装依赖

```bash
pip install -e .
pip install -e ".[dev]"  # 包含测试依赖
```

#### 3.2 配置 settings.yaml

编辑 `config/settings.yaml`，关键字段说明：

```yaml
llm:                           # LLM 生成模型配置
  provider: "openai"           # openai / azure / ollama / gemini / qwen / deepseek
  model: "gpt-4o"
  api_key: "sk-xxx"           # 支持环境变量 ${VAR_NAME}

embedding:                     # Embedding 模型配置
  provider: "openai"           # openai / azure / ollama / qwen / local
  model: "text-embedding-3-small"
  dimensions: 1536

vector_store:                  # 向量存储
  provider: "chroma"
  persist_directory: "./data/db/chroma"
  collection_name: "knowledge_hub"

retrieval:                     # 检索参数
  dense_top_k: 20
  sparse_top_k: 20
  fusion_top_k: 10
  rrf_k: 60

rerank:                        # 重排序
  enabled: true
  provider: "llm"              # llm / cross_encoder / none
  top_k: 5

evaluation:                    # 评估
  enabled: false
  provider: "custom"           # custom / ragas
  metrics: ["hit_rate", "mrr"]

observability:                 # 可观测性
  log_level: "INFO"
  trace_enabled: true
  trace_file: "./logs/traces.jsonl"

ingestion:                     # 数据摄取
  chunk_size: 1000
  chunk_overlap: 200
  splitter: "recursive"
  batch_size: 100
```

### 4. MCP 配置示例

将 MCP Server 接入 AI 工具，只需在对应工具的配置文件中添加本项目：

#### GitHub Copilot (VS Code)

在你的项目根目录创建 `.vscode/mcp.json`：

```json
{
  "servers": {
    "modular-rag": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONPATH": "${workspaceFolder}/src"
      }
    }
  }
}
```

#### Claude Desktop

编辑 `claude_desktop_config.json`（通常在 `%APPDATA%\Claude\` 或 `~/Library/Application Support/Claude/`）：

```json
{
  "mcpServers": {
    "modular-rag": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "D:/MODULAR-RAG-MCP-SERVER",
      "env": {
        "PYTHONPATH": "D:/MODULAR-RAG-MCP-SERVER/src"
      }
    }
  }
}
```

配置后重启对应工具，即可在对话中调用 `query_knowledge_hub`、`list_collections`、`get_document_summary` 三个 MCP Tool。

### 5. Dashboard 使用指南

#### 启动命令

```bash
# 方式一：使用启动脚本
python scripts/start_dashboard.py

# 方式二：直接启动
PYTHONPATH=src streamlit run src/observability/dashboard/app.py
```

默认访问 `http://localhost:8501`。

#### 六大页面功能

| 页面 | 功能 |
|------|------|
| **System Overview** | 展示所有组件配置卡片（LLM / Embedding / VectorStore / Retrieval / Reranker / Evaluation），实时数据统计（Chroma 条目数、BM25 文档数、图片数） |
| **Data Browser** | 浏览已摄入文档列表（支持集合筛选），展开查看 Chunk 详情（文本内容、Metadata），预览关联图片 |
| **Ingestion Manager** | 上传文件（PDF / TXT / MD 等）触发摄取，实时进度条展示各阶段进度，支持 Force Re-ingest 和跨存储协调删除 |
| **Ingestion Traces** | 摄取历史列表，阶段耗时瀑布图（Bar Chart），展示 load / split / transform / encode / upsert 各环节耗时 |
| **Query Traces** | 查询历史列表（支持关键词搜索），Dense vs Sparse 结果数对比，Rerank 前后排名变化分析 |
| **Evaluation** | 选择评估后端（Custom / Ragas）与 Golden Test Set，运行评估并展示 Hit Rate / MRR 等指标，支持历史报告查看 |

### 6. 项目架构可视化

项目内置一个交互式代码可视化工具，基于 **Cytoscape.js** + **Mermaid**，可在浏览器中直观查看模块间的依赖关系与数据流。

#### 快速启动

```bash
cd code-viz
npm install    # 首次使用需要安装依赖
npm start      # 启动可视化服务（默认 http://localhost:3456）
```

> 需要 **Node.js 18+**。首次启动时 `prestart` 脚本会自动同步前端库文件。

#### 使用方式

1. 启动后在浏览器打开 `http://localhost:3456`
2. 可视化面板会展示项目全部模块的依赖拓扑图（core / libs / ingestion / mcp_server 等）
3. 支持节点点击、缩放、搜索高亮，可实时查看各模块间的调用关系
4. 修改源码后页面会通过 SSE 热刷新，无需手动刷新浏览器

#### 生成静态报告

```bash
cd code-viz
python parser/extract.py    # 解析源码，生成 public/metadata.json
```

生成的 `metadata.json` 是所有模块关系的数据源，供前端渲染使用。

#### 跨项目复用

该工具是独立的 Node.js 项目，可直接复制 `code-viz/` 目录到其他 Python 项目使用，只需修改 `server.js` 中的 `ROOT` 路径指向新的项目根目录即可。

---

### 7. 运行测试

```bash
# 单元测试（281 个）
pytest tests/unit/ -q

# 集成测试（需要 ChromaDB + 本地依赖）
pytest tests/integration/ -q

# E2E 测试（全链路）
pytest tests/e2e/ -q

# 全部测试
pytest tests/ -q

# 跳过 LLM 依赖的测试
pytest tests/ -q -m "not slow"
```

