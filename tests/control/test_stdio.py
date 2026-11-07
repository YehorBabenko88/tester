"""Real MCP SDK subprocess and loopback HTTP integration, no installed service."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import threading
import unittest


class StdioTests(unittest.TestCase):
    def test_sdk_initialization_catalog_and_authenticated_call(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                data = json.dumps({"actions":["history.coverage"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "src.control.bridge"],
                env={"LAB_CONTROL_URL":f"http://127.0.0.1:{server.server_port}",
                     "LAB_CONTROL_TOKEN":"integration-test-credential-"+"x"*32})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    self.assertEqual(len(catalog.tools), 8)
                    result = await session.call_tool("control_capabilities", {})
                    self.assertFalse(result.isError)
                    self.assertIn("history.coverage", str(result.content))
                    rejected = await session.call_tool("control_approve", {})
                    self.assertTrue(rejected.isError)
        try:
            asyncio.run(asyncio.wait_for(exercise(), 20))
            self.assertEqual(seen, [("/api/control/capabilities", "Bearer integration-test-credential-"+"x"*32)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__": unittest.main()
