"""Build Tally Import XML and inspect its explicit operation counters."""

import xml.etree.ElementTree as ET

from app.xml_parser import TallyError, parse_xml_root


MASTER_TAGS = {"ledgers": "LEDGER", "parties": "LEDGER", "stock-groups": "STOCKGROUP", "stock-items": "STOCKITEM"}
SUCCESS_TAGS = {"Create": "CREATED", "Alter": "ALTERED", "Delete": "DELETED"}


def _add(parent, tag, value):
    ET.SubElement(parent, tag).text = str(value)


def _envelope(company, report):
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    for tag, value in (("VERSION", "1"), ("TALLYREQUEST", "Import"), ("TYPE", "Data"), ("ID", report)):
        _add(header, tag, value)
    body = ET.SubElement(root, "BODY")
    desc = ET.SubElement(body, "DESC")
    variables = ET.SubElement(desc, "STATICVARIABLES")
    _add(variables, "SVCURRENTCOMPANY", company)
    message = ET.SubElement(ET.SubElement(body, "DATA"), "TALLYMESSAGE")
    return root, message


def build_master_import(entity, action, data):
    root, message = _envelope(data.company, "All Masters")
    original = getattr(data, "original_name", None) or data.name
    node = ET.SubElement(message, MASTER_TAGS[entity], NAME=original, ACTION=action)
    if action != "Delete":
        _add(node, "NAME", data.name)
        if data.parent is not None:
            _add(node, "PARENT", data.parent or "Primary")
        if entity == "stock-items" and data.base_units:
            _add(node, "BASEUNITS", data.base_units)
        if entity == "stock-items" and action == "Create":
            names = ET.SubElement(node, "NAME.LIST", TYPE="String")
            _add(names, "NAME", data.name)
        if entity == "stock-items":
            for field, tag in (("opening_quantity", "OPENINGBALANCE"),
                               ("opening_rate", "OPENINGRATE"), ("opening_value", "OPENINGVALUE")):
                value = getattr(data, field)
                if value is not None and value != "":
                    _add(node, tag, value)
        if entity in ("ledgers", "parties") and data.opening_balance is not None:
            _add(node, "OPENINGBALANCE", data.opening_balance)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_voucher_import(action, data):
    root, message = _envelope(data.company, "Vouchers")
    attrs = {"VCHTYPE": data.voucher_type, "ACTION": action}
    if action != "Create":
        attrs.update({"DATE": data.original_date.strftime("%d-%b-%Y") if action == "Alter" else data.date.strftime("%d-%b-%Y"),
                      "TAGNAME": "Voucher Number", "TAGVALUE": data.original_number if action == "Alter" else data.voucher_number,
                      "VCHTYPE": data.original_type if action == "Alter" else data.voucher_type})
    voucher = ET.SubElement(message, "VOUCHER", attrs)
    if action != "Delete":
        _add(voucher, "DATE", data.date.strftime("%Y%m%d"))
        _add(voucher, "VOUCHERTYPENAME", data.voucher_type)
        if data.voucher_number:
            _add(voucher, "VOUCHERNUMBER", data.voucher_number)
        if data.narration:
            _add(voucher, "NARRATION", data.narration)
        entries = data.ledger_entries or []
        inventory_entries = data.inventory_entries or []
        party = next((entry.ledger_name for entry in entries if entry.is_party), None)
        if party:
            _add(voucher, "PARTYLEDGERNAME", party)
        if action == "Create" or entries or inventory_entries:
            _add(voucher, "PERSISTEDVIEW", "Invoice Voucher View" if inventory_entries else "Accounting Voucher View")
            _add(voucher, "ISINVOICE", "Yes" if inventory_entries else "No")
        for entry in entries:
            line = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _add(line, "LEDGERNAME", entry.ledger_name)
            _add(line, "ISDEEMEDPOSITIVE", "Yes" if entry.amount < 0 else "No")
            _add(line, "ISPARTYLEDGER", "Yes" if entry.is_party else "No")
            _add(line, "AMOUNT", entry.amount)
            if entry.bill_reference:
                bill = ET.SubElement(line, "BILLALLOCATIONS.LIST")
                _add(bill, "NAME", entry.bill_reference)
                _add(bill, "BILLTYPE", entry.bill_type or "New Ref")
                _add(bill, "AMOUNT", entry.amount)
        for entry in inventory_entries:
            line = ET.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
            sale = data.voucher_type.casefold().startswith("sales")
            amount = entry.quantity * entry.rate * (1 if sale else -1)
            _add(line, "STOCKITEMNAME", entry.stock_item)
            _add(line, "ISDEEMEDPOSITIVE", "No" if sale else "Yes")
            _add(line, "ACTUALQTY", f"{entry.quantity} {entry.unit}")
            _add(line, "BILLEDQTY", f"{entry.quantity} {entry.unit}")
            _add(line, "RATE", f"{entry.rate}/{entry.unit}")
            _add(line, "AMOUNT", amount)
            if entry.godown or entry.batch_name:
                batch = ET.SubElement(line, "BATCHALLOCATIONS.LIST")
                _add(batch, "GODOWNNAME", entry.godown or "Main Location")
                _add(batch, "BATCHNAME", entry.batch_name or "Primary Batch")
                _add(batch, "DESTINATIONGODOWNNAME", entry.godown or "Main Location")
                _add(batch, "AMOUNT", amount)
                _add(batch, "ACTUALQTY", f"{entry.quantity} {entry.unit}")
                _add(batch, "BILLEDQTY", f"{entry.quantity} {entry.unit}")
            allocation = ET.SubElement(line, "ACCOUNTINGALLOCATIONS.LIST")
            _add(allocation, "LEDGERNAME", entry.ledger_name)
            _add(allocation, "ISDEEMEDPOSITIVE", "No" if sale else "Yes")
            _add(allocation, "AMOUNT", amount)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_import_result(payload, action):
    root = parse_xml_root(payload)
    values = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].upper()
        if tag in ("CREATED", "ALTERED", "DELETED", "ERRORS", "IGNORED", "CANCELLED", "LASTVCHID"):
            try:
                values[tag] = int((node.text or "0").strip())
            except ValueError:
                raise TallyError("Tally ka import result valid nahi hai.")
    error = next(((node.text or "").strip() for node in root.iter()
                  if node.tag.rsplit("}", 1)[-1].upper() == "LINEERROR"), "")
    status = next(((node.text or "").strip() for node in root.iter()
                   if node.tag.rsplit("}", 1)[-1].upper() == "STATUS"), "")
    expected = SUCCESS_TAGS[action]
    if status == "0" or error or values.get("ERRORS", 0) or values.get(expected, 0) != 1:
        raise TallyError(error or f"Tally ne {action.lower()} confirm nahi kiya. Response: {values or 'no import counters'}")
    return {"action": action.lower(), "counters": values, "message": f"Tally me {action.lower()} successful."}
