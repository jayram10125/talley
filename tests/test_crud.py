"""Write tests use fake responses only; they never change a real Tally company."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.write_models import MasterDelete, MasterUpdate, MasterWrite, VoucherDelete, VoucherUpdate, VoucherWrite
from app.xml_import import build_master_import, build_voucher_import, parse_import_result
from app.xml_parser import TallyError


COMPANY = "Demo & Co"


class CrudTests(unittest.TestCase):
    def test_full_http_import_round_trip_with_fake_tally(self):
        imports = []

        class FakeTally(BaseHTTPRequestHandler):
            def do_POST(self):
                request = self.rfile.read(int(self.headers["Content-Length"]))
                root = ET.fromstring(request)
                if root.findtext("./HEADER/TALLYREQUEST") == "Import":
                    imports.append(root)
                    payload = b"<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED><ERRORS>0</ERRORS></RESPONSE>"
                elif root.findtext("./BODY/DESC/TDL/TDLMESSAGE/COLLECTION/TYPE") == "Company":
                    payload = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION><COMPANY NAME='Demo &amp; Co'/></COLLECTION></DATA></BODY></ENVELOPE>"
                else:
                    payload = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION/></DATA></BODY></ENVELOPE>"
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
            with TestClient(app) as client:
                response = client.post("/api/records/ledgers", json={"host": "localhost", "port": server.server_port,
                    "company": COMPANY, "name": "New Ledger", "parent": "Sundry Debtors"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(imports[0].findtext("./BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY"), COMPANY)
            self.assertEqual(imports[0].findtext("./BODY/DATA/TALLYMESSAGE/LEDGER/NAME"), "New Ledger")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_master_create_alter_delete_requests(self):
        create = MasterWrite(company=COMPANY, name="Laptop", parent="Electronics", base_units="Nos", opening_quantity="2 Nos")
        xml = ET.fromstring(build_master_import("stock-items", "Create", create))
        self.assertEqual(xml.findtext("./BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY"), COMPANY)
        self.assertEqual(xml.findtext("./BODY/DATA/TALLYMESSAGE/STOCKITEM/BASEUNITS"), "Nos")
        self.assertEqual(xml.findtext("./BODY/DATA/TALLYMESSAGE/STOCKITEM/OPENINGBALANCE"), "2 Nos")
        alter = MasterUpdate(company=COMPANY, original_name="Laptop", name="New Laptop", parent="Electronics")
        xml = ET.fromstring(build_master_import("stock-items", "Alter", alter))
        self.assertEqual(xml.find("./BODY/DATA/TALLYMESSAGE/STOCKITEM").attrib["NAME"], "Laptop")
        self.assertEqual(xml.findtext("./BODY/DATA/TALLYMESSAGE/STOCKITEM/NAME"), "New Laptop")
        delete = MasterDelete(company=COMPANY, name="Laptop")
        xml = ET.fromstring(build_master_import("stock-items", "Delete", delete))
        self.assertEqual(xml.find("./BODY/DATA/TALLYMESSAGE/STOCKITEM").attrib["ACTION"], "Delete")

    def test_voucher_create_update_delete_requests(self):
        data = dict(company=COMPANY, date="2026-10-01", voucher_type="Sales", voucher_number="7",
                    ledger_entries=[{"ledger_name": "Customer", "amount": "-100", "is_party": True, "bill_reference": "7"},
                                    {"ledger_name": "Sales", "amount": "100"}],
                    inventory_entries=[{"stock_item": "Keyboard", "quantity": "1", "unit": "Nos",
                                        "rate": "100", "ledger_name": "Sales", "godown": "Main Location"}])
        create = VoucherWrite(**data)
        xml = ET.fromstring(build_voucher_import("Create", create))
        voucher = xml.find("./BODY/DATA/TALLYMESSAGE/VOUCHER")
        self.assertEqual(voucher.findtext("DATE"), "20261001")
        self.assertEqual(len(voucher.findall("ALLLEDGERENTRIES.LIST")), 2)
        self.assertEqual(voucher.findtext("ALLLEDGERENTRIES.LIST/BILLALLOCATIONS.LIST/NAME"), "7")
        self.assertEqual(voucher.findtext("ALLINVENTORYENTRIES.LIST/ACCOUNTINGALLOCATIONS.LIST/AMOUNT"), "100")
        self.assertEqual(voucher.findtext("ALLINVENTORYENTRIES.LIST/BATCHALLOCATIONS.LIST/GODOWNNAME"), "Main Location")
        update = VoucherUpdate(company=COMPANY, date="2026-10-01", voucher_type="Sales",
                               original_date="2026-10-01", original_type="Sales", original_number="7",
                               narration="Updated")
        voucher = ET.fromstring(build_voucher_import("Alter", update)).find("./BODY/DATA/TALLYMESSAGE/VOUCHER")
        self.assertEqual(voucher.attrib["TAGVALUE"], "7")
        self.assertEqual(voucher.findtext("NARRATION"), "Updated")
        self.assertEqual(len(voucher.findall("ALLLEDGERENTRIES.LIST")), 0)
        delete = VoucherDelete(company=COMPANY, date="2026-10-01", voucher_type="Sales", voucher_number="7")
        voucher = ET.fromstring(build_voucher_import("Delete", delete)).find("./BODY/DATA/TALLYMESSAGE/VOUCHER")
        self.assertEqual(voucher.attrib["ACTION"], "Delete")

    def test_tally_import_counters_are_required(self):
        success = b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        self.assertEqual(parse_import_result(success, "Create")["counters"]["CREATED"], 1)
        with self.assertRaises(TallyError):
            parse_import_result(b"<RESPONSE><CREATED>0</CREATED><ERRORS>0</ERRORS></RESPONSE>", "Create")
        with self.assertRaises(TallyError):
            parse_import_result(b"<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Bad ledger</LINEERROR></RESPONSE>", "Create")

    def test_api_create_update_delete_and_validation(self):
        async def fake_fetch(_host, _port, view, _company=""):
            if view == "companies":
                return [{"Name": COMPANY}]
            return [{"Name": "Old Ledger"}]

        with patch("app.main.fetch_tally", new=AsyncMock(side_effect=fake_fetch)), \
             patch("app.main.import_tally", new=AsyncMock(return_value={"action": "create", "message": "ok", "counters": {"CREATED": 1}})) as imported:
            with TestClient(app) as client:
                base = {"host": "localhost", "port": 9000, "company": COMPANY}
                create = client.post("/api/records/ledgers", json={**base, "name": "New Ledger", "parent": "Sundry Debtors"})
                self.assertEqual(create.status_code, 200)
                self.assertEqual(imported.await_count, 1)
                duplicate = client.post("/api/records/ledgers", json={**base, "name": "Old Ledger", "parent": "Sundry Debtors"})
                self.assertEqual(duplicate.status_code, 409)
                update = client.patch("/api/records/ledgers", json={**base, "original_name": "Old Ledger", "name": "Changed Ledger", "parent": "Sundry Debtors"})
                self.assertEqual(update.status_code, 200)
                delete = client.request("DELETE", "/api/records/ledgers", json={**base, "name": "Old Ledger"})
                self.assertEqual(delete.status_code, 200)
                self.assertEqual(imported.await_count, 3)
                invalid = client.post("/api/records/vouchers", json={**base, "date": "2026-10-01", "voucher_type": "Sales",
                    "ledger_entries": [{"ledger_name": "Customer", "amount": "-100"}, {"ledger_name": "Sales", "amount": "90"}]})
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(imported.await_count, 3)


if __name__ == "__main__":
    unittest.main()
