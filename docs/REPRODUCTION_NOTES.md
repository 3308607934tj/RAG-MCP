# 复刻排错记录（Reproduction Notes）

> 本文记录在复刻 [kidoom/RAG-MCP](https://github.com/kidoom/RAG-MCP)（基线提交 `8194ca2`）过程中
> **实际遇到并解决**的问题。每条按「现象 → 定位过程 → 根因 → 处理 → 可迁移的知识点」组织，
> 保留真实的报错原文与文件行号，便于复核。
>
> 复刻环境：Windows 11 · conda (Python 3.11) · Clash Verge 代理 · Claude Code 桌面端

## 目录

| # | 问题 | 类型 |
|---|------|------|
| 01 | Git 走代理报 `SEC_E_NO_CREDENTIALS` | 网络/TLS |
| 02 | `python` 命令指向 0 字节 Store 占位别名 | 环境隔离 |
| 03 | README 声称支持 `${VAR}` 环境变量，代码未实现 | 文档与实现不符 |
| 04 | README 的 YAML 示例缺 4 个必填项 | 文档与实现不符 |
| 05 | CLI 摄取不写 trace，Dashboard 追踪页永远为空 | 可观测性覆盖缺口 |
| 06 | `tags` 序列化/反序列化不对称 | **缺陷（已修复）** |
| 07 | `persist_directory` 相对路径依赖进程 cwd | 缺陷（已定位） |
| 08 | RRF 分数"低且全部相同"并非 bug | 算法理解 |
| 09 | Claude Code 桌面端工具列表里没有本服务器 | 客户端接入 |
| 10 | LLM 重排"看起来没生效"的误判 | 排错方法 |
| 11 | 测试隔离污染：单跑通过、全量失败 | **测试隔离（已修复）** |
| 12 | eager import 让可插拔架构在依赖层面失效 | 架构分析（已记录） |
| 13 | PDF 摄取失败：`markitdown` 缺少 PDF extra | **依赖缺陷（已修复）** |
| 14 | 单元测试因构造函数副作用污染生产向量库 | **测试隔离（已修复）** |

---

## 01. Git 走代理报 `SEC_E_NO_CREDENTIALS`

**现象**

```
fatal: unable to access 'https://github.com/kidoom/RAG-MCP.git/':
schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030E)
```

**定位过程**

1. 先按"网络被封"处理 → `Test-NetConnection github.com -Port 443` 失败
2. 但同一时刻 `codeload.github.com:443` **可以**连接 → 说明不是整体封锁
3. 换 TLS 后端重试 `git -c http.sslBackend=openssl clone ...` → 报错变成
   `Recv failure: Connection was reset`（说明 schannel 那层问题被绕过了，剩下的是网络层）
4. 枚举本机监听端口，发现 `127.0.0.1:7897` 开放（Clash Verge 混合端口）
5. **同时**指定 openssl 后端与代理 → clone 成功

**根因**

两个独立问题叠加，只解决其中一个都不行：

- Windows 系统代理（注册表 `ProxyEnable=1`, `127.0.0.1:7897`）**Git 不读取**
- Git for Windows 默认 `http.sslBackend=schannel`，而在走 HTTP 代理时无法获取客户端凭据

**处理**

```powershell
git config --global http.sslBackend openssl
git config --global http.https://github.com.proxy http://127.0.0.1:7897
```

用 `http.<url>.proxy` 按域名精确匹配，而不是全局 `http.proxy` —— 这样国内仓库（gitee 等）仍走直连。

**可迁移的知识点**

- `schannel` 与 `openssl` 两个 TLS 后端的行为差异
- `http.<url>.proxy` 的 URL 前缀匹配机制（可用 `git config --get-urlmatch http.proxy <url>` 验证）
- 系统代理 ≠ 应用代理；git / pip / curl 各自独立

---

## 02. `python` 命令指向 0 字节 Store 占位别名

**现象**

```cmd
> python -c "import sys; print(sys.version)"
（无任何输出，退出码 1）
```

**定位过程**

```powershell
Get-Command python    # → C:\Users\...\WindowsApps\python.exe
Get-Item  ...\WindowsApps\python.exe | Select Length    # → Length : 0
```

同一个 PATH 里 `WindowsApps` 排在真实 Python（`D:\anaconda3`）之前。

**根因**

`WindowsApps\python.exe` 是 Microsoft Store 的 **App Execution Alias 占位文件**（0 字节）。
当没有安装商店版 Python 时，执行它不报"找不到命令"，而是静默失败。

**处理**

用 conda 环境隔离，激活后环境自带的 python 在 PATH 中优先：

```powershell
conda create -n rag-mcp python=3.11 -y
conda activate rag-mcp
where.exe python    # 第一条应为 F:\anaconda\envs\rag-mcp\python.exe
```

**可迁移的知识点**

- Windows PATH 优先级与 Store 别名机制
- 用 `where.exe`（按优先级列出全部匹配）而非 `Get-Command`（只给第一个）排查命令来源
- 虚拟环境隔离同时解决了"多套 Python 互相污染"的问题

---

## 03. README 声称支持 `${VAR}` 环境变量，代码未实现

**现象**

`config/settings.yaml` 中写 `api_key: "${DEEPSEEK_API_KEY}"`，调用 LLM 时认证失败。

**定位过程**

```python
settings = load_settings()
print(repr(settings.llm.api_key))
# 输出：'${DEEPSEEK_API_KEY}'   ← 字面量，没有被展开
```

随后全仓库搜索变量展开逻辑：

```
grep -rE 'expandvars|\$\{|os\.environ|getenv|load_dotenv' src/   →  0 命中
```

**根因**

`src/core/settings.py` 只做 `yaml.safe_load()`，**没有任何环境变量展开**。
README 第 106 行注释"支持环境变量 `${VAR_NAME}`"是**未实现的承诺**。

**处理**

把真实 key 直接写进 `config/settings.yaml`（该文件因此被排除在版本控制之外）。

**可迁移的知识点**

- **以源码为准验证文档**，尤其是"看起来像功能"的占位符语法
- 配置加载链路的完整追踪：YAML → 校验 → dataclass → 工厂 → SDK
- 正确做法本应是在 `settings.py` 中实现展开（这也是一个可提 PR 的改进点）

---

## 04. README 的 YAML 示例缺 4 个必填项

**现象**

按 README「3.2 配置 settings.yaml」的示例建成配置文件后：

```
SettingsError: Missing required field: settings.vision_llm
```

**定位过程**

逐项补齐后错误依次变化，暴露出全部缺口：

```
settings.vision_llm  →  llm.temperature  →  llm.max_tokens  →  observability.structured_logging
```

对照 `src/core/settings.py` 的 `Settings.from_dict()`，这些字段都由
`_require_*()` 强制校验，而 README 示例只给了简化片段（整个 `vision_llm` 节都没有）。

**根因**

文档示例与 `from_dict` 的必填校验不同步。仓库自带的 `config/settings.yaml` 才是完整合法的。

**处理**

使用仓库自带的配置，只替换 `api_key` 等少量字段。

**可迁移的知识点**

- 强校验配置系统的价值：错误立即暴露，而不是运行时才失败
- 文档示例应作为**最小可运行样例**维护，否则会持续误导使用者

---

## 05. CLI 摄取不写 trace，Dashboard 追踪页永远为空

**现象**

`python scripts/ingest_files.py` 摄取成功后：

- `logs/` 目录**不存在**
- Dashboard 的 "Ingestion Traces" 页面为空

**定位过程**

```
grep -rn 'write_trace' src/ scripts/
```

只有两处调用：

| 位置 | 场景 |
|------|------|
| `scripts/query.py:96` | 命令行查询 |
| `src/observability/dashboard/pages/ingestion_manager.py:29` | 网页端摄取 |

`scripts/ingest.py` / `scripts/ingest_files.py` **没有挂 `TraceCollector`**。

**根因**

`write_trace()`（`src/observability/logger.py:68`）是唯一创建 `logs/traces.jsonl` 的入口，
CLI 摄取链路没有接入。属于**可观测性覆盖缺口**，非环境问题。

**处理**

记录在案。若需要 CLI 摄取也留痕，需自行在脚本中挂载 `TraceCollector(on_collect=write_trace)`。

**可迁移的知识点**

- 可观测性要覆盖**全部入口**，否则会出现"功能正常但观测缺失"的假象
- 排查"页面为空"时，先确认**数据源是否被写入**，而不是先怀疑前端

---

## 06. `tags` 序列化/反序列化不对称（已修复）

**现象**

MCP 工具 `get_document_summary` 返回：

```json
"tags": ["[\"code\", \"mcp\", \"modular\", \"rag\", \"server\", \"modular-rag\"]"]
```

一个**单元素数组**，里面塞着整串 JSON 文本；正确形态应是 6 个独立标签。

**定位过程**

沿数据流三段排查：

| 阶段 | 位置 | 行为 |
|------|------|------|
| 写入 | `src/ingestion/transform/metadata_enricher.py:252` | `md["tags"] = tags`，存的是**真正的 list** ✓ |
| 落库 | `src/libs/vector_store/chroma_store.py:46-56` | `_sanitize_metadata()` 把 list **`json.dumps` 成字符串** ✓（Chroma 只接受 str/int/float/bool，这一步是被迫且正确的） |
| 读取 | `src/mcp_server/tools/get_document_summary.py:48-55` | ❌ 读到 `str` 而非 `list`，直接走 `elif tags_raw:` 套成单元素数组，**没有反向 `json.loads`** |

**根因**

**写侧编码正确，读侧漏了解码** —— 典型的序列化不对称。Chroma 的 metadata 类型约束是诱因。

**处理**

在读侧补上解码，且只对"能解析成 JSON 数组的字符串"生效，避免误伤普通字符串标签：

```python
tags_raw = metadata.get("tags")
if isinstance(tags_raw, str):
    try:
        decoded = json.loads(tags_raw)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if isinstance(decoded, list):
        tags_raw = decoded
```

**可迁移的知识点**

- 存储层有类型约束时，**编解码必须成对出现**，且应集中在同一层
- 修 bug 时要沿完整数据流逐段验证，才能定位到"哪一段错了"
- 修复要收敛影响面（此改动不影响未编码的普通字符串）

---

## 07. `persist_directory` 相对路径依赖进程 cwd（已定位）

**现象**

MCP 客户端启动服务后调用 `query_knowledge_hub`，返回空结果，且**没有任何报错**。

**定位过程**

1. 手动运行 `python -m mcp_server.server` 却正常 → 差异在**启动时的工作目录**
2. 检查配置解析：`src/core/settings.py` 中确实存在 `resolve_path()`（第 28 行），
   但全局搜索其调用点发现**只用在配置文件自身**（第 594 行）
3. `persist_directory` 被**原样**传给 `chromadb.PersistentClient(path=...)`
   （`src/libs/vector_store/chroma_store.py:66`）→ 由 Chroma 按**进程 cwd** 解析

**根因**

配置里的 `./data/db/chroma` 是相对路径，实际解析基准是进程工作目录而不是项目根。
MCP 客户端的工作目录不可预期，于是服务在别处新建了一个**空库**，表现为"静默返回空结果"。

**处理**

- 本地：把该配置项改为绝对路径规避
- 正解：在 `Settings.from_dict()` 或各调用侧用已有的 `resolve_path()` 归一化
  （注意分层约束：`libs` 可以依赖 `core.types`，但**不应依赖 `core.settings`**，
  所以修复点应放在 `core` 或调用方，而非 `chroma_store.py`）

**可迁移的知识点**

- 进程工作目录陷阱：相对路径在多入口/多客户端场景下不可靠
- 最危险的不是报错，而是**静默失败**（查空库不抛异常）
- 修复位置要服从既有分层约束，而不是就近改

---

## 08. RRF 分数"低且全部相同"并非 bug

**现象**

`--verbose` 输出中，4 个候选的融合分数**全部约等于 0.0320**，且重排前后排列完全一致，
一度怀疑分块重复或融合失效。

**定位过程**

手算 RRF：`score = Σ 1/(k + rank)`，其中 `k = rrf_k = 60`。

4 个候选在 Dense / Sparse 两条链路上只是互换名次，因此只可能产生两个值：

| 组合 | 算式 | 结果 |
|------|------|------|
| 一路第 1 + 另一路第 4 | 1/61 + 1/64 | 0.032018 |
| 一路第 2 + 另一路第 3 | 1/62 + 1/63 | 0.032002 |

**根因**

RRF 是**排名倒数求和**，其数值量级与余弦相似度**不可比**。
理论上限为 `2/(k+1) = 2/61 ≈ 0.0328` —— 实测的 0.0320 已经**接近满分**，不是低分。

另外核实分块内容：4 个 chunk 分别是不同章节（项目信息 / 启用步骤 / 工具列表 / 问题排查），
**不存在重复**。它们同源于一份文档，是因为当时知识库里**只有这一篇文档**。

**处理**

无需修复。同时在排查中发现一个真实的可观测性小缺陷：
`scripts/query.py:128-132` 调用 `reranker.rerank()` 时**未传入 `trace`**，
因此 trace 里不会出现 `rerank` 阶段 —— 这**不代表发生了回退**，只是没打点。

**可迁移的知识点**

- 判断检索质量不能看 RRF 绝对分值，要看排名变化与命中情况
- 排错时**先算理论值/理论上限**，能快速区分"异常"与"符合数学预期"
- 数据量太小时，检索结果同源是必然，不代表分块或融合有问题

---

## 09. Claude Code 桌面端工具列表里没有本服务器

**现象**

会话中列出的 MCP 工具只有 `cowork`、`scheduled-tasks`、`session_info`、`skills`、`workspace`，
没有 `modular-rag`。

**定位过程**

1. 检查配置文件：`.mcp.json` 位置与格式均正确（项目根、`mcpServers` 包裹）
2. 换个角度看**工具构成**：出现了 `request_cowork_directory`（申请访问本机文件夹）
   与 `workspace.bash`（**隔离的 Linux 环境**）—— 说明该会话运行在沙箱中，
   而非本机。
3. 结论：本地 `stdio` 服务器需要在本机启动进程，沙箱/云端会话无法拉起。

**根因**

会话的环境选错。此外还发现：Claude Code 的 MCP stdio 配置**只支持
`type` / `command` / `args` / `env`**，**不支持 `cwd` 字段**（官方文档未列出），
所以不能依赖 `cwd` 解决相对路径问题（这正是第 07 条的关联项）。

**处理**

在桌面端使用 **Code 标签**，并把两个选择器设为：

| 选择器 | 必须 |
|--------|------|
| Environment | **Local** |
| Project folder | 仓库根目录（`.mcp.json` 所在目录） |

**可迁移的知识点**

- MCP 的 `stdio` 传输**必须本地进程**，远程环境无法使用
- 换环境排查比换配置文件更快：**先看工具列表反推运行环境**
- 客户端作用域优先级可能与 CLI 不同（桌面端 `~/.claude.json` 优先于 `.mcp.json`）

---

## 10. LLM 重排"看起来没生效"的误判

**现象**

`--verbose` 输出中 `Rerank` 阶段的分数与 `Fusion` 阶段**完全一致**，排名也没有变化，
初步判断"重排静默回退了"。

**定位过程**

这个初判**被自己推翻了**，过程值得记录：

1. 查回退实现：`src/core/query_engine/reranker.py:160-178` 的 `_fallback()` 确实会
   **原样复制 `item.score`** —— 但 `src/libs/reranker/llm_reranker.py:149` 在**成功时也只重排顺序、不改分数**
   → 所以"分数相同"在**成功与回退两种情况下都会出现，不能作为证据**
2. 查 trace：`logs/traces.jsonl` 里没有 `rerank` 阶段 —— 但很快发现
   `scripts/query.py:128-132` 调用时**根本没传 `trace`** → 同样**不能作为证据**
3. 唯一剩下的线索是"顺序没变"，但这既可能是回退，也可能只是 LLM 认同了原顺序

**处理**

设计**对照实验**：直调重排后端，故意把最不相关的候选放在首位。

```python
candidates = [irrelevant(score=0.90), relevant(score=0.10), partial(score=0.50)]
backend.rerank("VS Code 怎么接入 MCP", candidates)
# 返回：['relevant', 'partial', 'irrelevant']   ← 生效
```

另外单独验证 LLM 通路本身可用（`llm.generate("只回复两个字：成功")` → `'成功'`）。

**根因**

无缺陷。真正的教训是**证据链的强度**：前两条"证据"都被推翻了，
若没有做对照实验就下结论，会去修一个不存在的问题。

**可迁移的知识点**

- 区分"符合回退特征"与"证明发生了回退"——**弱证据不能叠加成强结论**
- 设计**对照实验**（打乱输入顺序）比读代码更能定性
- 排查时先确认"日志/追踪是否真的覆盖了该路径"，否则会把"没打点"误读为"没执行"

---

## 11. 测试隔离污染：单跑通过、全量失败（已修复）

**现象**

```
pytest tests/unit -q
→ 10 failed, 331 passed, 1 skipped
```

10 个失败**全部集中在** `tests/unit/test_document_chunker.py`，错误完全相同：

```
AttributeError: 'module' object at ingestion.chunking has no attribute 'chunking'
```

而单独跑该文件却是 **15 passed**。典型的"单跑通过、全量失败"。

**定位过程**

1. **先排除自身改动**：失败测试的导入路径是 `core.types` → `core.settings` → `ingestion.chunking` → `libs.splitter`，与本地适配改过的文件无关
2. **排除 pytest 版本兼容**：`pyproject.toml` 声明 `pytest>=7.0.0`，实际装的是 9.1.1；但单跑通过说明不是版本行为差异
3. **写诊断脚本**逐步复刻 `_pytest.monkeypatch.resolve()` 的逻辑 → 三步 `getattr` **全部成功**，证明解析逻辑本身没问题，问题在**模块状态**
4. **搜索测试套件中对 `sys.modules` 的操作** → 发现 3 个文件在**模块级**直接覆盖 `sys.modules` 且从不还原
5. **做三组对照实验**（见下）→ 预测全部命中，锁死因果

**根因**

`tests/unit/test_image_captioner_fallback.py` 在模块导入时执行（原始版本第 22-24 行）：

```python
_ingestion = ModuleType("ingestion")        # 造一个只有 __path__ 的假包
_ingestion.__path__ = [str(_SRC / "ingestion")]
sys.modules["ingestion"] = _ingestion       # 覆盖真包，且从不还原
```

pytest 在**收集阶段**就会导入所有测试模块，而文件名按字母序 `test_document_chunker.py`（d）**先于** `test_image_captioner_fallback.py`（i）：

1. chunker 被导入时，真实 `ingestion` 包加载，`chunking` 属性挂在**真包对象**上
2. 随后 captioner 把 `sys.modules["ingestion"]` 换成**假包**（假包上没有 `chunking`）
3. 测试运行时，`monkeypatch.setattr("ingestion.chunking.document_chunker.SplitterFactory.create", ...)`：
   - `importlib.import_module("ingestion")` → 拿到**假包**
   - `getattr(假包, "chunking")` → AttributeError
   - 兜底 `importlib.import_module("ingestion.chunking")` → 真子模块**早已在 `sys.modules`**，所以不报错
   - pytest 于是在**旧的假父对象**上再取一次属性 → 抛出那个带路径注释的错误

**对照实验（决定性）**

| 组 | 命令 | 结果 |
|---|------|------|
| A | `pytest tests/unit/test_document_chunker.py -q` | 15 passed |
| B | `pytest .../test_document_chunker.py .../test_image_captioner_fallback.py -q` | **10 failed, 16 passed** |
| C | `pytest .../test_image_captioner_fallback.py .../test_document_chunker.py -q` | **26 passed** |

**同一批测试、仅交换收集顺序，结果相反** —— 这是收集期 `sys.modules` 污染的铁证。

C 组能通过的原因：假包先装好，之后 chunker 的 `from ingestion.chunking import ...` 会把**真子模块挂到假父对象上**，`getattr` 就成功了。这正好解释了为什么全量跑会失败（字母序 d 在 i 之前）。

**处理**

在 `test_image_captioner_fallback.py` 中改为**快照 + 还原**：注入前记录真实的 `sys.modules` 条目，`exec_module` 完成后还原。

之所以不用"直接 pop"：`sys.modules["ingestion"] = 假包` 这一步会**丢掉真模块的引用**，只 pop 会导致后续任何 `import ingestion` 都要重新执行 `__init__.py`，白付一次重导入开销。快照还原则把真模块原样放回。

结果：`10 failed, 331 passed` → **`341 passed, 1 skipped`**

**可迁移的知识点**

- **模块级修改 `sys.modules` 是测试隔离的反模式**，应使用 `monkeypatch.setitem(sys.modules, ...)`（自动还原）或手工快照还原
- "单跑通过、全量失败"几乎总是指向**测试间状态污染**或**收集顺序依赖**
- pytest 的收集阶段会导入所有测试模块 —— 模块级副作用发生在**任何测试运行之前**
- 用**交换顺序的对照实验**锁死因果，而不是接受"符合污染特征"这类弱证据

---

## 12. eager import 让可插拔架构在依赖层面失效（已记录）

**现象**

第 11 条的对照实验里出现一个异常数据：同样的测试集合，只因收集顺序不同，耗时相差 **7 秒**。

| 组 | 场景 | 耗时 |
|---|------|------|
| A / B | 真实导入 `ingestion` 包 | 8.5s |
| C | 假 `ingestion` 包（跳过了 `__init__.py`） | **1.42s** |

**定位过程**

沿 `ingestion/__init__.py` 的导入链逐层下钻：

```
ingestion/__init__.py:9                        from .embedding import BatchProcessor, DenseEncoder, SparseEncoder
└→ ingestion/embedding/dense_encoder.py:10     from libs.embedding import BaseEmbedding, EmbeddingFactory, EmbeddingSettings
   └→ libs/embedding/__init__.py:56            from .huggingface_embedding import HuggingFaceEmbedding
      └→ libs/embedding/huggingface_embedding.py:2   from sentence_transformers import SentenceTransformer
         └→ torch（约 7 秒）
```

**根因**

`libs/embedding/__init__.py` 在**模块级**导入了全部 6 个 provider，其中 `huggingface_embedding`
第 2 行就是 `from sentence_transformers import SentenceTransformer`。

后果：**只要 `import ingestion`（甚至只是 `import libs.embedding`），就必须加载 torch** ——
哪怕配置里用的是 qwen / openai 这类纯 API provider，根本不碰本地模型。

这也解释了第 11 条里那个 `sys.modules` hack 的**动机**：作者正是为了绕开这个强制的 7 秒 + torch 依赖，才去手工伪造命名空间。**表层 bug 背后是架构诱因。**

**处理**

本次仅记录，未改动（属架构级调整，风险与收益需单独评估）。可选的三层修法：

| 层次 | 修法 | 收益 | 风险 |
|------|------|------|------|
| 表层 | 测试端快照还原（第 11 条已完成） | 消除测试间污染 | 低 |
| 中层 | `ingestion/__init__.py` 改用 PEP 562 懒加载（模块级 `__getattr__`） | `import ingestion` 从 8.5s 降到秒级 | 中，需全量回归 |
| 深层 | `libs/embedding/__init__.py` 懒加载各 provider | `sentence-transformers` 才真正成为**按需依赖** | 中 |

**可迁移的知识点**

- **工厂模式只解决了"运行时选择"，不解决"依赖加载"** —— 若 `__init__.py` 把全部实现 eager import，插件的依赖就变成了强制的
- 插件式架构中，重依赖应放在 provider 模块内部延迟导入，或由工厂在实例化时才导入
- **导入耗时是可量化的架构指标**：1.42s vs 8.5s 比任何"应该懒加载"的论断都有说服力
- 遇到"为了绕开某问题而写的奇怪 hack"时，先问**为什么需要这个 hack**

---

## 13. PDF 摄取失败：`markitdown` 缺少 PDF extra（已修复）

**现象**

在 Dashboard「摄取管理」上传任意 PDF，管线跑完但报错，消息里只有一句无提示的提取失败：

```
失败：<文件名> — <底层库异常>
```

而同样流程摄取 `.md` 文件一切正常。

**定位过程**

1. **先缩小范围**：Markdown 摄取此前已成功 6 篇 → 说明管线本身没问题，问题在 **PDF 专用分支**
2. **列出该分支的两个依赖**：
   - `MarkItDown` —— 负责 PDF → Markdown 的**文字提取**（`pdf_loader.py:138`）
   - `pypdf` —— 负责**抽图片**（`pdf_loader.py:214`）
3. **检查实际安装情况**：

   | 包 | 状态 |
   |---|---|
   | `markitdown 0.1.7` | ✅ |
   | `pypdf` | ✅ |
   | **`pdfminer-six`** | ❌ **未安装** |
   | **`pdfplumber`** | ❌ **未安装** |

4. **查 `markitdown` 的 METADATA**，发现 PDF 支持被放在 **extra** 里：

   ```
   Provides-Extra: pdf
   Requires-Dist: pdfminer-six>=20251230; extra == 'pdf'
   Requires-Dist: pdfplumber>=0.11.9;   extra == 'pdf'
   ```

5. **对照 `pyproject.toml:21`** —— 只写了 `markitdown>=0.1.0`，**不带 extra**

**根因**

依赖声明不完整。按 `pyproject.toml` 安装后，`markitdown` 能 `import`，但**没有能力解析 PDF**。

而 `pdf_loader.py:137-147` 只对 `import` 做了保护，**没有保护 `.convert()`**：

```python
try:
    from markitdown import MarkItDown
except ImportError as exc:                      # ← 只保护了这里
    raise ImportError("...Install it with: pip install markitdown")

converter = MarkItDown()
result = converter.convert(path)                # ← 真正的失败点，异常裸奔
```

于是用户看到的是底层库的原始异常，**没有任何可操作提示** —— 这是一次典型的"错误处理不完整导致排错成本翻倍"。

**处理**

| # | 改动 | 文件 |
|---|------|------|
| 1 | 依赖声明补上 extra：`markitdown>=0.1.0` → `markitdown[pdf]>=0.1.0` | `pyproject.toml` |
| 2 | 把 `.convert()` 也包进 try/except，失败时提示 `pip install "markitdown[pdf]"` 并附上原始错误类型与信息 | `src/libs/loader/pdf_loader.py` |
| 3 | README 的安装命令补上 `pdf-images` extra（见下方附带发现） | `README.md` |

即时解封：`pip install "markitdown[pdf]"`

> ⚠️ 不要用 `markitdown[all]` —— 它会拉进 Azure AI SDK、YouTube 转录、pydub 等大量无关依赖。只需要 `[pdf]`。

**附带发现：PDF 还需要 `pypdf`，但 README 没提**

摄取前的**质量门禁**（`quality_check`，对 PDF 生效）会调用 pypdf 抽样前几页，缺它时抛 `ImportError` 导致摄取失败。而 `pypdf` 被放在 `pdf-images` extra 里，README 的安装命令却是 `pip install -e ".[dev]"` —— **不含该 extra**。

所以要"按文档装完就能摄 PDF"，需要两个条件同时成立：

```bash
pip install -e ".[dev,pdf-images]"      # pypdf
pip install "markitdown[pdf]"           # 现在已由 pyproject 直接声明
```

**可迁移的知识点**

- **extra 依赖是"装上了却不能用"的常见陷阱**：`pip install X` 成功 **≠** X 的全部功能可用
- 报错缺少可操作提示时，**先去看依赖包 METADATA 里的 extra 列表**
- 包装第三方异常时**必须保留原始错误类型与信息**，不要吞掉 —— 否则排查成本成倍上升
- 一个功能（PDF）的可用性可能**分散在多个依赖声明处**（base deps / 各 extra / 文档），任何一处漏掉都会表现为"功能不可用"

---

## 14. 单元测试因构造函数副作用污染生产向量库（已修复）

**现象**

`list_collections`（以及 MCP 工具）里出现了一个名为 `test` 的集合，而配置中从未定义过它，也没有人手动创建过任何集合。

**定位过程**

1. 最初的猜测是"后台有摄取任务或别的会话在操作同一个库" —— **排除**：没有任何并发操作
2. 搜索测试套件中的 `persist_directory`，发现多个测试的 YAML fixture 写着
   `persist_directory: ./data/db/chroma` + `collection_name: test`
3. **但逐个核对后发现大部分是"无害的"**：
   - `test_dense_retriever.py`、`test_sparse_retriever.py` 都注入了 `FakeVectorStore`，
     那两个配置字符串**从未被真正使用**
   - `test_chroma_store_roundtrip.py`、`test_image_storage.py`、`test_bm25_indexer_roundtrip.py`、
     `test_file_integrity.py` 等**已正确使用 `tmp_path`**
4. 最终锁定唯一真凶 —— `tests/unit/test_pipeline_progress.py:70-74`：

```python
def test_pipeline_constructor_does_not_require_on_progress() -> None:
    settings = Settings.from_dict(yaml.safe_load(_MINIMAL_SETTINGS_YAML))
    pipeline = IngestionPipeline(settings)        # 只是"构造对象"
    assert pipeline is not None
```

**根因**

**构造函数带副作用**：

```
IngestionPipeline(settings)
  └→ pipeline.py:86            VectorUpserter(settings)
      └→ chroma_store.py:66    chromadb.PersistentClient(path="./data/db/chroma")
      └→ chroma_store.py:67    client.get_or_create_collection("test")   ← 写盘
```

`get_or_create_collection` 在集合不存在时会**创建**它。于是一个"只断言对象构造成功"的测试，
**每次跑单元测试都会往生产向量库里写入一个 `test` 集合**。

（同时它还会触碰 `data/db/ingestion_history.db`、`data/db/bm25/`、`data/images/` —— 这些是 REPO_ROOT 下的真实生产路径，但构造函数只创建目录、无可观察产物，因此只有 Chroma 集合暴露了问题。）

**处理**

1. 该测试改用 `tmp_path`：把 YAML 里的 `./data/db/chroma` 替换为临时目录
2. 清理历史残留：删除已存在的 `test` 集合
3. **验证**：清理后再跑一次全量单元测试，`test` 集合**不再出现**

**可迁移的知识点**

- **构造函数应当廉价**。"构造即创建外部资源（集合 / 文件 / 连接）"会让所有只需要一个实例的代码 —— 包括测试 —— 都产生副作用
- 判断测试是否会污染，**不能只看配置文件里的路径字符串**，要看它是否真的被用于实例化真实对象；注入了 Fake 的不会
- **看起来无副作用的测试也可能写盘**（`assert obj is not None` 就足以触发）
- 排查数据污染时，先问"**谁写的**"，再看"写了什么"：把嫌疑范围从"所有相关文件"收敛到"真正实例化真实对象的那个"

---

## 附：这些问题对应的知识域

（与项目自带学习体系的 `D1`–`D10` 知识域对应，便于串讲）

| 问题 | 知识域 |
|------|--------|
| 01, 09 | D5 MCP Server 协议 · D6 可插拔架构 |
| 02 | 工程化 / 环境隔离 |
| 03, 04 | D6 Settings 配置加载 |
| 05, 08 | D8 可观测性与评估体系 |
| 06 | D2 存储层协同 · D5 Tool 注册机制 |
| 07 | D6 配置系统 · D2 存储层协同 |
| 10 | D4 Rerank 机制 |
| 11 | D9 测试策略与工程化 |
| 12 | D6 可插拔架构 · D9 测试策略 |
| 13 | D7 PDF 解析 · D9 工程化（依赖与打包） |
| 14 | D9 测试策略 · D2 存储层协同 |
