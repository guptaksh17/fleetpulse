"""
API integration tests against the real PostgreSQL, TimescaleDB and Redis (Phase 6).
Creates its own users (random passwords) and removes them afterwards. Skipped, with the
reason printed, when the databases are not reachable.
"""

import json
import os
import secrets
import sys
import time
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

try:
    import psycopg2
    PG = psycopg2.connect(os.getenv("POSTGRES_DSN", "host=localhost port=5434 dbname=fleetpulse user=fleetpulse password=fleetpulse"))
    PG.autocommit = True
    with PG.cursor() as c:
        c.execute("SELECT count(*) FROM component_risk")
        HAS_SCORES = c.fetchone()[0] > 0
    SKIP = None if HAS_SCORES else "no component_risk rows (run ml/score.py batch first)"
except Exception as e:  # noqa: BLE001
    SKIP = f"database not reachable: {e}"

if SKIP is None:
    from fastapi.testclient import TestClient
    from app import main as api_main
    from app.security import hash_password

T1 = "00000000-0000-0000-0000-000000000001"


@unittest.skipIf(SKIP is not None, SKIP or "")
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api_main.app)
        cls.pw = secrets.token_urlsafe(12)
        cls.suffix = uuid.uuid4().hex[:8]
        with PG.cursor() as c:
            c.execute("SELECT tenant_id::text FROM tenant WHERE tenant_id <> %s LIMIT 1", (T1,))
            cls.t2 = c.fetchone()[0]
            for role, tenant in (("ADMIN", None), ("FLEET_MANAGER", T1), ("VIEWER", cls.t2)):
                c.execute("INSERT INTO app_user (email, password_hash, role, tenant_id) VALUES (%s, %s, %s, %s)",
                          (f"test-{role.lower()}-{cls.suffix}@test.local", hash_password(cls.pw), role, tenant))
            c.execute("""SELECT v.vehicle_id::text FROM vehicle v JOIN fleet f USING (fleet_id)
                         JOIN vehicle_component vc ON vc.vehicle_id = v.vehicle_id JOIN component_risk r ON r.vehicle_component_id = vc.vehicle_component_id
                         WHERE f.tenant_id = %s LIMIT 1""", (T1,))
            cls.t1_vehicle = c.fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        with PG.cursor() as c:
            c.execute("DELETE FROM app_user WHERE email LIKE %s", (f"test-%-{cls.suffix}@test.local",))

    def token(self, role):
        r = self.client.post("/api/v1/auth/login", json={"email": f"test-{role}-{self.suffix}@test.local", "password": self.pw})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def test_login_rejects_bad_password_and_requires_token(self):
        r = self.client.post("/api/v1/auth/login", json={"email": f"test-admin-{self.suffix}@test.local", "password": "wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get("/api/v1/summary").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/summary", headers={"Authorization": "Bearer not-a-jwt"}).status_code, 401)
        self.assertIn("error", self.client.get("/api/v1/summary").json())

    def test_tenant_isolation(self):
        mgr, viewer = self.token("fleet_manager"), self.token("viewer")
        self.assertEqual(self.client.get(f"/api/v1/vehicles/{self.t1_vehicle}", headers=mgr).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/vehicles/{self.t1_vehicle}", headers=viewer).status_code, 404)
        items = self.client.get("/api/v1/priority?limit=200", headers=viewer).json()["items"]
        self.assertNotIn(self.t1_vehicle, {i["vehicle_id"] for i in items})
        with PG.cursor() as c:
            c.execute("SELECT count(*) FROM vehicle v JOIN fleet f USING (fleet_id) WHERE f.tenant_id = %s", (T1,))
            t1_count = c.fetchone()[0]
        self.assertEqual(self.client.get("/api/v1/summary", headers=mgr).json()["vehicles"], t1_count)

    def test_roles(self):
        viewer = self.token("viewer")
        self.assertEqual(self.client.post(f"/api/v1/alerts/{uuid.uuid4()}/acknowledge", headers=viewer).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/audit", headers=viewer).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/audit?limit=5", headers=self.token("admin")).status_code, 200)

    def test_priority_keyset_pagination_is_ordered_and_disjoint(self):
        h = self.token("admin")
        p1 = self.client.get("/api/v1/priority?limit=10", headers=h).json()
        p2 = self.client.get(f"/api/v1/priority?limit=10&cursor={p1['next_cursor']}", headers=h).json()
        losses = [i["expected_loss"] for i in p1["items"] + p2["items"]]
        self.assertEqual(losses, sorted(losses, reverse=True))
        self.assertFalse({i["vehicle_component_id"] for i in p1["items"]} & {i["vehicle_component_id"] for i in p2["items"]})
        self.assertEqual(self.client.get("/api/v1/priority?cursor=%%%", headers=h).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/priority?limit=100000", headers=h).status_code, 422)

    def test_location_is_coarsened_for_non_admin(self):
        mgr = self.client.get(f"/api/v1/vehicles/{self.t1_vehicle}/telemetry?limit=5", headers=self.token("fleet_manager")).json()["items"]
        adm = self.client.get(f"/api/v1/vehicles/{self.t1_vehicle}/telemetry?limit=5", headers=self.token("admin")).json()["items"]
        self.assertTrue(mgr and adm)
        for r in mgr:
            self.assertEqual(r["latitude"], round(r["latitude"], 2))

    def _api_audit_count(self, email):
        with PG.cursor() as c:
            c.execute("SELECT count(*) FROM audit_log WHERE actor_id = %s AND action LIKE 'API_%%'", (email,))
            return c.fetchone()[0]

    def _wait_for_count(self, email, expected, timeout=8.0):
        deadline = time.time() + timeout
        n = self._api_audit_count(email)
        while n < expected and time.time() < deadline:
            api_main.flush_audit()
            time.sleep(0.2)
            n = self._api_audit_count(email)
        return n

    def test_every_data_request_is_audited(self):
        email = f"test-fleet_manager-{self.suffix}@test.local"
        h = self.token("fleet_manager")
        # Settle: earlier queued rows (batched off the request path) must land before the baseline.
        before = self._api_audit_count(email)
        before = self._wait_for_count(email, before + 10**6, timeout=api_main.AUDIT_FLUSH_SECONDS + 1.0)
        self.client.get("/api/v1/summary", headers=h)
        self.client.get("/api/v1/alerts", headers=h)
        self.assertEqual(self._wait_for_count(email, before + 2), before + 2)

    def test_rate_limit(self):
        h = self.token("viewer")
        old = api_main.RATE_LIMIT_PER_MINUTE
        api_main.RATE_LIMIT_PER_MINUTE = 3
        try:
            codes = [self.client.get("/api/v1/me", headers=h).status_code for _ in range(6)]
        finally:
            api_main.RATE_LIMIT_PER_MINUTE = old
        self.assertIn(429, codes)

    def test_driver_erasure(self):
        did = str(uuid.uuid4())
        with PG.cursor() as c:
            c.execute("INSERT INTO driver (driver_id, tenant_id, name, license_number) VALUES (%s, %s, 'Test Person', 'LIC-X')", (did, T1))
        try:
            self.assertEqual(self.client.post(f"/api/v1/drivers/{did}/erase", headers=self.token("fleet_manager")).status_code, 403)
            r = self.client.post(f"/api/v1/drivers/{did}/erase", headers=self.token("admin"))
            self.assertEqual(r.status_code, 200)
            with PG.cursor() as c:
                c.execute("SELECT name, license_number FROM driver WHERE driver_id = %s", (did,))
                self.assertEqual(c.fetchone(), (None, None))
                c.execute("SELECT count(*) FROM audit_log WHERE action = 'DRIVER_ERASED' AND entity_id = %s", (did,))
                self.assertEqual(c.fetchone()[0], 1)
        finally:
            with PG.cursor() as c:
                c.execute("DELETE FROM driver WHERE driver_id = %s", (did,))


@unittest.skipIf(SKIP is not None, SKIP or "")
class TestAssistant(unittest.TestCase):
    """Fleet assistant: rules fallback, guardrails, tenant scope, audit, and the LLM tool loop (mocked)."""

    @classmethod
    def setUpClass(cls):
        TestApi.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        TestApi.tearDownClass.__func__(cls)

    token = TestApi.token

    def test_rules_mode_answers_from_tools(self):
        os.environ.pop("GROQ_API_KEY", None)
        r = self.client.post("/api/v1/assistant", json={"question": "What should I service first?"}, headers=self.token("fleet_manager"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["mode"], "rules")
        self.assertEqual(body["tools_used"][0]["tool"], "top_priority")
        self.assertIn("7-day risk", body["answer"])
        r2 = self.client.post("/api/v1/assistant", json={"question": "Which batteries are most at risk?"}, headers=self.token("fleet_manager")).json()
        self.assertEqual(r2["tools_used"][0]["args"].get("component"), "BATTERY")

    def test_guardrails(self):
        h = self.token("fleet_manager")
        self.assertEqual(self.client.post("/api/v1/assistant", json={"question": "x" * 501}, headers=h).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/assistant", json={"question": "  "}, headers=h).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/assistant", json={"question": "hi"}).status_code, 401)
        from app import assistant
        with self.assertRaises(assistant.ToolError):
            assistant.run_tool("drop_table", {}, None)
        with self.assertRaises(assistant.ToolError):
            assistant.run_tool("vehicle_detail", {"vin": "' OR 1=1 --"}, None)
        self.assertEqual(assistant._limit({"limit": 10**6}), 20)

    def test_tenant_scope_and_audit(self):
        from app import assistant
        from app.security import Principal
        with PG.cursor() as c:
            c.execute("SELECT v.vin FROM vehicle v JOIN fleet f USING (fleet_id) WHERE f.tenant_id = %s LIMIT 1", (T1,))
            vin = c.fetchone()[0]
        viewer = Principal("u", f"test-viewer-{self.suffix}@test.local", "VIEWER", self.t2)
        out = assistant.ask(f"Tell me about {vin}", viewer)
        self.assertIn("No vehicle", out["answer"], "another tenant's vehicle must not be visible")
        with PG.cursor() as c:
            c.execute("SELECT metadata FROM audit_log WHERE action = 'AI_ASSISTANT_QUERY' AND actor_id = %s ORDER BY occurred_at DESC LIMIT 1", (viewer.email,))
            meta = c.fetchone()[0]
        self.assertEqual(meta["tools"][0]["tool"], "vehicle_detail")

    def test_llm_tool_loop_with_mocked_groq(self):
        from app import assistant
        from app.security import Principal
        calls = []

        def fake_post(body):
            req = json.loads(body)
            calls.append(req)
            if len(calls) == 1:  # the model asks for a tool
                return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "top_priority", "arguments": json.dumps({"limit": 3, "component": "BATTERY"})}}]}}]}
            tool_msg = [m for m in req["messages"] if m["role"] == "tool"][0]
            items = json.loads(tool_msg["content"])["items"]
            return {"choices": [{"message": {"content": f"Top battery: {items[0]['vin']}"}}]}

        mgr = Principal("u", f"test-fleet_manager-{self.suffix}@test.local", "FLEET_MANAGER", T1)
        out = assistant.ask("Which batteries should I service?", mgr, post=fake_post)
        self.assertTrue(out["mode"].startswith("llm:"))
        self.assertEqual(out["tools_used"], [{"tool": "top_priority", "args": {"limit": 3, "component": "BATTERY"}}])
        self.assertTrue(out["answer"].startswith("Top battery: "))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["tools"][0]["function"]["name"], "fleet_summary")


if __name__ == "__main__":
    unittest.main()
