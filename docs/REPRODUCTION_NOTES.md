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
