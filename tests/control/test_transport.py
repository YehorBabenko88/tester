import asyncio
import unittest
from src.control.api import ControlAPI
from src.control.bridge import GatewayClient, create_mcp
from src.control.policy import TokenAuth
import test_plane as fixtures
from test_plane import NOW, request


class TransportTests(unittest.TestCase):
    tearDown = fixtures.PlaneTests.tearDown
    def setUp(self):
        fixtures.PlaneTests.setUp(self)
        self.auth = TokenAuth(clock=lambda: NOW)
        self.auth.register("a"*40, self.agent)
        self.auth.register("h"*40, self.human)
        self.api = ControlAPI(self.plane, self.auth, allowed_origin="http://127.0.0.1:8765", csrf_secret="c"*40)

    def call(self, method, path, body=None, **kwargs):
        return self.api.handle(method, path, body=body, bearer="a"*40, **kwargs)

    def test_api_auth_origin_csrf_and_no_worker_or_approval_tools(self):
        self.assertEqual(self.api.handle("GET", "/api/control/capabilities", bearer="bad")[0], 401)
        self.assertEqual(self.call("GET", "/api/control/capabilities", origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("POST", "/api/control/plan", request(), origin="http://127.0.0.1:8765")[0], 403)
        self.assertEqual(self.call("POST", "/api/control/claim", {})[0], 404)
        status, plan = self.call("POST", "/api/control/plan", request("system.config.apply", params={"expected_revision":0,"changes":{"max_workers":2}}))
        self.assertEqual(status, 200)
        path = "/api/control/approvals/"+plan["plan_id"]
        self.assertEqual(self.call("POST", path, {})[0], 403)
        self.assertEqual(self.api.handle("POST", path, body={}, bearer="h"*40)[0], 403)
        self.assertEqual(self.api.handle("POST", path, body={}, bearer="h"*40, local_human_channel=True, csrf="c"*40)[0], 200)

    def test_api_end_to_end_and_safe_errors(self):
        status, plan = self.call("POST", "/api/control/plan", request())
        self.assertEqual(status, 200)
        status, job = self.call("POST", "/api/control/jobs", {"plan_id":plan["plan_id"]})
        self.assertEqual(status, 202)
        self.assertEqual(self.call("GET", "/api/control/jobs/"+job["job_id"])[1]["state"], "queued")
        self.assertEqual(self.call("POST", "/api/control/jobs/"+job["job_id"]+"/cancel", {})[1]["state"], "cancelled")
        self.assertEqual(self.call("GET", "/api/control/jobs/"+job["job_id"]+"/events?limit=1")[0], 200)
        status, error = self.call("POST", "/api/control/plan", request(params={"token":"do-not-log"}))
        self.assertEqual(status, 400)
        self.assertNotIn("do-not-log", str(error)+str(self.plane.audit(self.agent)))
        self.assertIn("denied", str(self.plane.audit(self.agent)))

    def test_bridge_rejects_remote_urls_and_has_fixed_tool_surface(self):
        for url in ("https://example.com", "http://localhost.evil", "http://127.0.0.1@evil", "http://127.0.0.1:8765/path", "http://127.0.0.1:8765?token=x"):
            with self.subTest(url=url), self.assertRaises(ValueError): GatewayClient(url, "a"*40)
        client = GatewayClient("http://127.0.0.1:8765", "a"*40)
        server = create_mcp(client)
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(names, {"control_capabilities", "control_plan", "control_submit", "control_job", "control_cancel", "control_events", "control_artifacts", "control_audit"})


if __name__ == "__main__": unittest.main()
