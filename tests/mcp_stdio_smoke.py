"""Real subprocess MCP handshake, SQLite SELECT and write rejection (source or exe)."""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def check(command, args, home):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    env = {**os.environ, "NIQ_HOME": str(home), "OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "",
           "KEYRING_SERVICE": "niq-test-" + home.name}
    async with Client(StdioServerParameters(command=command, args=args, env=env), read_timeout_seconds=90) as client:
        tools = await client.list_tools()
        assert {"run_sql", "ask", "status"} <= {t.name for t in tools.tools}
        status = await client.call_tool("status", {})
        assert not status.is_error
        result = await client.call_tool("run_sql", {"connection": "synthetic", "sql": "SELECT COUNT(*) AS n FROM orders"})
        data = result.structured_content.get("result", result.structured_content)
        assert data["success"] and data["rows"] == [{"n": 3}], data
        denied = await client.call_tool("run_sql", {"connection": "synthetic", "sql": "DELETE FROM orders"})
        data = denied.structured_content.get("result", denied.structured_content)
        assert data["success"] is False
    print(json.dumps({"passed": True, "checks": ["stdio-handshake", "tools", "SQLite-SELECT", "write-rejected"],
                      "executable": Path(command).name}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe")
    options = parser.parse_args()
    home = Path(tempfile.mkdtemp(prefix="niq-mcp-check-"))
    os.environ["NIQ_HOME"] = str(home)
    from backend.db.profiles import Profile, ProfileStore
    db = home / "synthetic.db"
    with sqlite3.connect(db) as connection:
        connection.executescript("CREATE TABLE orders(id INTEGER); INSERT INTO orders VALUES(1),(2),(3);")
    ProfileStore().save(Profile(type="sqlite", name="synthetic", database=str(db)))
    asyncio.run(check(options.exe or sys.executable, [] if options.exe else [str(ROOT / "niq_mcp.py")], home))


if __name__ == "__main__":
    main()
