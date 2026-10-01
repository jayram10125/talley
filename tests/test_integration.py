import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.main import app
from app.catalog import select_rows
from app.tally_client import fetch_tally, resolve_private_host
from app.xml_builder import build_collection_request
from app.xml_parser import TallyError, parse_collection_response


class TallyIntegrationTests(unittest.TestCase):
    def test_builder_escapes_company_and_includes_fields(self):
        root = ET.fromstring(build_collection_request("vouchers", "A & B", date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(root.findtext("./BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY"), "A & B")
        self.assertEqual(root.findtext("./BODY/DESC/STATICVARIABLES/SVFROMDATE"), "20261001")
        self.assertEqual(root.findtext("./BODY/DESC/TDL/TDLMESSAGE/COLLECTION/TYPE"), "Voucher")

    def test_parser_extracts_rows_and_rejects_errors(self):
        xml = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION><LEDGER NAME='Cash'><PARENT>&#4; Cash-in-Hand</PARENT><CLOSINGBALANCE>120</CLOSINGBALANCE></LEDGER></COLLECTION></DATA></BODY></ENVELOPE>"
        self.assertEqual(parse_collection_response(xml, "ledgers")[0]["ClosingBalance"], "120")
        self.assertEqual(parse_collection_response(xml, "ledgers")[0]["Name"], "Cash")
        self.assertEqual(parse_collection_response(xml, "ledgers")[0]["Parent"], "Cash-in-Hand")
        with self.assertRaises(TallyError):
            parse_collection_response(b"<ENVELOPE><HEADER><STATUS>0</STATUS></HEADER></ENVELOPE>", "ledgers")

    def test_stock_summary_sign_matches_tally_report(self):
        rows = [{"Name": "Hp Laptop", "ClosingBalance": "8 Nos",
                 "ClosingRate": "30000.00/Nos", "ClosingValue": "-240000.00"}]
        self.assertEqual(select_rows("stock-summary", rows)[0]["ClosingValue"], "240000.00")

    def test_private_host_boundary(self):
        self.assertEqual(resolve_private_host("localhost"), "127.0.0.1")
        self.assertEqual(resolve_private_host("192.168.1.12"), "192.168.1.12")
        with self.assertRaises(TallyError):
            resolve_private_host("169.254.169.254")

    def test_http_xml_round_trip(self):
        received = []

        class FakeTally(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                payload = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION><COMPANY><NAME>Demo Company</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>"
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTally)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rows = asyncio.run(fetch_tally("127.0.0.1", server.server_port, "companies"))
            self.assertEqual(rows, [{"Name": "Demo Company"}])
            self.assertEqual(ET.fromstring(received[0]).findtext("./HEADER/TALLYREQUEST"), "Export")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_connection_to_company_to_data_flow(self):
        async def fake_fetch(host, port, view_name, company="", from_date=None, to_date=None):
            if view_name == "companies":
                return [{"Name": "Demo Company"}]
            return [{"Name": "Cash", "Parent": "Cash-in-Hand", "OpeningBalance": "0",
                     "ClosingBalance": "120", "MasterID": "1"}]

        with patch("app.main.fetch_tally", new=AsyncMock(side_effect=fake_fetch)):
            with TestClient(app) as client:
                tested = client.post("/api/connection/test", json={"host": "localhost", "port": 9000})
                self.assertEqual(tested.status_code, 200)
                self.assertEqual(tested.json()["companies"][0]["Name"], "Demo Company")
                data = client.get("/api/data/ledgers", params={"host": "localhost", "port": 9000, "company": "Demo Company"})
                self.assertEqual(data.status_code, 200)
                self.assertEqual(data.json()["rows"][0]["Name"], "Cash")
                csv_response = client.get("/api/export/ledgers.csv", params={"company": "Demo Company"})
                self.assertEqual(csv_response.status_code, 200)
                self.assertIn("Cash", csv_response.text)


if __name__ == "__main__":
    unittest.main()
