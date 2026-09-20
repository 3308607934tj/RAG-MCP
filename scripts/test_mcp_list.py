"""Quick smoke test: exercise all three MCP tools via subprocess JSON-RPC.

Covers:
  1. list_collections
  2. query_knowledge_hub
  3. get_document_summary   (doc_id is taken from step 2's citations)

Run from the repository root with the project environment activated:
    python scripts/test_mcp_list.py
"""

import json
import os
import subprocess
import sys

# Print unencodable characters as '?' instead of crashing on narrow consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

env = os.environ.copy()
env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src")

PYTHON = sys.executable

QUERY = "VS Code 怎么接入 MCP"


def call_tool(name, arguments=None, timeout=120):
    """Send one tools/call request to a fresh MCP server process."""
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        ensure_ascii=False,
    ) + "\n"

    proc = subprocess.Popen(
        [PYTHON, "-m", "mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
        env=env,
    )
    stdout, stderr = proc.communicate(input=request.encode("utf-8"), timeout=timeout)

    if stderr:
        for line in stderr.decode("utf-8", errors="replace").split("\n"):
            if any(k in line for k in ["ERROR", "Error", "Traceback", "Exception"]):
                print("  STDERR:", line[:200])

    raw = stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        print("  没有收到任何响应（服务可能启动失败）")
        return None
    try:
        return json.loads(raw.splitlines()[0])
    except json.JSONDecodeError:
        print("  响应不是合法 JSON，原始输出：")
        print("   ", raw[:500])
        return None


def show(resp):
    """Print a JSON-RPC response in readable form; return structuredContent."""
    if resp is None:
        return {}
    if "error" in resp:
        print("  JSON-RPC 错误:", json.dumps(resp["error"], ensure_ascii=False))
        return {}

    result = resp.get("result") or {}
    structured = result.get("structuredContent")
    if structured is not None:
        print("  structuredContent:", json.dumps(structured, ensure_ascii=False)[:600])

    for block in result.get("content") or []:
        if block.get("type") == "text":
            text = block.get("text", "")
            print("  text:")
            for line in text[:400].splitlines():
                print("    ", line)

    return structured if isinstance(structured, dict) else {}


print("=== 1/3 list_collections ===")
show(call_tool("list_collections"))

print("\n=== 2/3 query_knowledge_hub ===")
structured = show(call_tool("query_knowledge_hub", {"query": QUERY, "top_k": 3}))

citations = structured.get("citations") or []
if not citations:
    print("\n=== 3/3 get_document_summary 已跳过 ===")
    print("  上一步没有返回 citations，取不到 doc_id。请先摄取数据：")
    print("    python scripts/ingest_files.py --path \"docs\" --extensions .md")
    raise SystemExit(0)

doc_id = citations[0].get("chunk_id")
print(f"\n=== 3/3 get_document_summary (doc_id 取自上一步) ===")
print(f"  doc_id = {doc_id}")
show(call_tool("get_document_summary", {"doc_id": doc_id}))
