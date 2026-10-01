"""Parse a bounded Tally XML response into JSON-ready rows."""

import re
import xml.etree.ElementTree as ET

from app.catalog import VIEWS


MAX_RESPONSE_BYTES = 12 * 1024 * 1024
CHARACTER_REFERENCE = re.compile(rb"&#(?:[xX]([0-9A-Fa-f]+)|([0-9]+));")


class TallyError(Exception):
    pass


def _name(element):
    return element.tag.rsplit("}", 1)[-1].upper()


def _remove_invalid_character_reference(match):
    """Tally may emit &#4; before 'Primary', which XML 1.0 forbids."""
    value = int(match.group(1), 16) if match.group(1) else int(match.group(2))
    valid = value in (9, 10, 13) or 32 <= value <= 0xD7FF or 0xE000 <= value <= 0xFFFD or 0x10000 <= value <= 0x10FFFF
    return match.group(0) if valid else b""


def parse_xml_root(payload: bytes):
    if len(payload) > MAX_RESPONSE_BYTES:
        raise TallyError("Response bahut bada hai. Chhoti date range try karein.")
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise TallyError("Tally se unsafe XML response mila.")
    payload = CHARACTER_REFERENCE.sub(_remove_invalid_character_reference, payload)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise TallyError("Endpoint ne valid Tally XML nahi bheja.") from exc
    if _name(root) not in ("ENVELOPE", "RESPONSE"):
        raise TallyError("Endpoint Tally XML server jaisa response nahi de raha.")
    return root


def parse_collection_response(payload: bytes, view_name: str) -> list:
    root = parse_xml_root(payload)
    status = next((n.text for n in root.iter() if _name(n) == "STATUS"), None)
    if status and status.strip() == "0":
        detail = next((n.text for n in root.iter() if _name(n) == "LINEERROR"), None)
        raise TallyError(detail or "Tally ne request reject kar di. Company aur HTTP settings check karein.")
    if not status or status.strip() != "1":
        raise TallyError("Endpoint se successful Tally XML status nahi mila.")
    view = VIEWS[view_name]
    wanted = view.object_type.replace(" ", "").upper()
    rows = []
    for node in root.iter():
        if _name(node) != wanted:
            continue
        children = {_name(child): (child.text or "").strip() for child in node}
        row = {field: children.get(field.upper(), "") for field in view.fields}
        if "Name" in row and not row["Name"]:
            row["Name"] = next((value for key, value in node.attrib.items() if key.upper() == "NAME"), "")
        if any(row.values()):
            rows.append(row)
    return rows
