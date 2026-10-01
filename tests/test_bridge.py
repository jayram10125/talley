import asyncio
import base64
import sqlite3
import tempfile
import threading
import time
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

import app.main as main
from app.bridge import BridgeStore
from connector.agent import run, validate_job, validate_server


COMPANIES_XML = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION><COMPANY><NAME>Cloud Demo</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>"
LEDGERS_XML = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION><LEDGER NAME='Cash'><PARENT>Cash-in-Hand</PARENT></LEDGER></COLLECTION></DATA></BODY></ENVELOPE>"


class BridgeTests(unittest.TestCase):
    def test_queue_pairing_and_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BridgeStore(str(Path(directory) / "bridge.sqlite3"))
            pair = store.pair_code()
            registration = store.register(pair["code"])
            self.assertIsNone(store.register(pair["code"]))
            self.assertEqual(store.authenticate(registration["token"]), pair["connector_id"])

            async def round_trip():
                task = asyncio.create_task(store.dispatch(pair["connector_id"], b"<ENVELOPE/>"))
                await asyncio.sleep(0.01)
                job = store.claim(pair["connector_id"])
                self.assertEqual(job["xml"], "<ENVELOPE/>")
                self.assertTrue(store.finish(pair["connector_id"], job["id"], COMPANIES_XML))
                return await task

            self.assertEqual(asyncio.run(round_trip()), COMPANIES_XML)
            self.assertTrue(store.revoke(pair["connector_id"]))
            self.assertIsNone(store.authenticate(registration["token"]))
            store.close()

    def test_cloud_api_requires_admin_and_uses_connector(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BridgeStore(str(Path(directory) / "bridge.sqlite3"))
            with patch.object(main, "CLOUD_MODE", True), patch.object(main, "BRIDGE", store), \
                 patch.object(main, "ADMIN_USER", "owner"), patch.object(main, "ADMIN_PASSWORD", "long-secret-password-123"):
                with TestClient(main.app) as client:
                    self.assertEqual(client.get("/api/bridge/connectors").status_code, 401)
                    auth = ("owner", "long-secret-password-123")
                    pair = client.post("/api/bridge/pair-codes", auth=auth, json={"tally_port": 9001}).json()
                    registered = client.post("/api/bridge/register", json={"code": pair["code"]}).json()
                    self.assertEqual(registered["tally_port"], 9001)
                    agent_headers = {"Authorization": "Bearer " + registered["token"]}
                    listed = client.get("/api/bridge/connectors", auth=auth)
                    self.assertEqual(listed.status_code, 200)
                    self.assertEqual(listed.headers["cache-control"], "no-store")
                    self.assertEqual(listed.json()[0]["id"], registered["connector_id"])
                    stop = threading.Event()

                    def agent():
                        with TestClient(main.app) as agent_client:
                            completed = 0
                            while not stop.is_set():
                                response = agent_client.get("/api/bridge/agent/jobs", headers=agent_headers)
                                if response.status_code == 200 and response.json():
                                    job = response.json()
                                    self.assertIn(b"<ENVELOPE>", validate_job(job["xml"]))
                                    result_xml = COMPANIES_XML if completed == 0 else LEDGERS_XML
                                    agent_client.post("/api/bridge/agent/jobs/" + job["id"],
                                                      headers=agent_headers,
                                                      json={"xml_base64": base64.b64encode(result_xml).decode()})
                                    completed += 1
                                    if completed == 2:
                                        return
                                time.sleep(0.02)

                    worker = threading.Thread(target=agent, daemon=True)
                    worker.start()
                    try:
                        result = client.post("/api/connection/test", auth=auth,
                                             json={"connector_id": registered["connector_id"]})
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertEqual(result.json()["companies"], [{"Name": "Cloud Demo"}])
                        rows = client.get("/api/data/ledgers", auth=auth,
                                          params={"connector_id": registered["connector_id"], "company": "Cloud Demo"})
                        self.assertEqual(rows.status_code, 200, rows.text)
                        self.assertEqual(rows.json()["rows"][0]["Name"], "Cash")
                    finally:
                        stop.set()
                        worker.join(timeout=2)
            store.close()

    def test_remote_http_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_server("http://example.com")
        self.assertEqual(validate_server("http://localhost:8000/"), "http://localhost:8000")

    def test_existing_bridge_database_migrates_port_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old.sqlite3")
            db = sqlite3.connect(path)
            try:
                db.execute("CREATE TABLE pair_codes (code_hash TEXT PRIMARY KEY, connector_id TEXT NOT NULL, expires_at REAL NOT NULL)")
                db.commit()
            finally:
                db.close()
            store = BridgeStore(path)
            pair = store.pair_code(9001)
            self.assertEqual(store.register(pair["code"])["tally_port"], 9001)
            store.close()

    def test_website_port_is_used_by_connector(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = threading.Event()
            calls = []

            def handler(request):
                calls.append(request.url.path)
                if request.url.path == "/api/bridge/register":
                    return httpx.Response(200, json={"connector_id": "demo", "token": "secret", "tally_port": 9001})
                stop.set()
                return httpx.Response(200, json=None)

            transport = httpx.MockTransport(handler)
            client_class = httpx.AsyncClient

            def client_factory(*_args, **_kwargs):
                return client_class(transport=transport)

            config_path = str(Path(directory) / "connector.json")
            states = []
            with patch("connector.agent.httpx.AsyncClient", new=client_factory):
                asyncio.run(run("https://example.com", "one-time-code", "localhost", 9000,
                                config_path, on_status=lambda state, detail: states.append((state, detail)),
                                stop_event=stop))
            self.assertEqual(json.loads(Path(config_path).read_text())["tally_port"], 9001)
            self.assertIn("localhost:9001", next(detail for state, detail in states if state == "online"))
            self.assertEqual(calls, ["/api/bridge/register", "/api/bridge/agent/jobs"])

    def test_render_local_mode_is_reported(self):
        with patch.dict("os.environ", {"RENDER": "true"}), patch.object(main, "CLOUD_MODE", False):
            self.assertEqual(main.config()["mode"], "local")
            self.assertIn("TALLY_MODE=cloud", main.config()["deployment_warning"])

    def test_published_windows_connector_download(self):
        if not main.CONNECTOR_BINARY.is_file():
            self.skipTest("Windows connector has not been built")
        with TestClient(main.app) as client:
            self.assertEqual(main.config()["connector_download_url"], "/downloads/TallyConnector.exe")
            response = client.get("/downloads/TallyConnector.exe")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content[:2], b"MZ")


if __name__ == "__main__":
    unittest.main()
