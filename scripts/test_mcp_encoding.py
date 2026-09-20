"""Quick smoke test: pipe a UTF-8 JSON-RPC request to the MCP server."""
import json
import subprocess
import os
import sys

request = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "query_knowledge_hub",
            "arguments": {"query": "VS Code 怎么接入 MCP", "top_k": 5},
        },
    },
    ensure_ascii=False,
) + "\n"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

env = os.environ.copy()
env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src")

proc = subprocess.Popen(
    [sys.executable, "-m", "mcp_server.server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd=REPO_ROOT,
    env=env,
)
stdout, stderr = proc.communicate(input=request.encode("utf-8"), timeout=60)
print("STDOUT:", stdout.decode("utf-8", errors="replace")[:500])
if stderr:
    err = stderr.decode("utf-8", errors="replace")
    for line in err.split("\n"):
        if any(k in line for k in ["ERROR", "Error", "Traceback", "Exception"]):
            print("STDERR:", line[:200])
