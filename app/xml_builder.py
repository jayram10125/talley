"""Tally XML request construction."""

from datetime import date
import xml.etree.ElementTree as ET

from app.catalog import VIEWS


def build_collection_request(view_name: str, company: str = "", from_date: date = None,
                             to_date: date = None) -> bytes:
    view = VIEWS[view_name]
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    for tag, value in (("VERSION", "1"), ("TALLYREQUEST", "Export"),
                       ("TYPE", "Collection"), ("ID", "Tally Connect Collection")):
        ET.SubElement(header, tag).text = value
    desc = ET.SubElement(ET.SubElement(root, "BODY"), "DESC")
    variables = ET.SubElement(desc, "STATICVARIABLES")
    ET.SubElement(variables, "SVEXPORTFORMAT").text = "$$SysName:XML"
    if company:
        ET.SubElement(variables, "SVCURRENTCOMPANY").text = company
    if from_date:
        ET.SubElement(variables, "SVFROMDATE").text = from_date.strftime("%Y%m%d")
    if to_date:
        ET.SubElement(variables, "SVTODATE").text = to_date.strftime("%Y%m%d")
    collection = ET.SubElement(ET.SubElement(ET.SubElement(desc, "TDL"), "TDLMESSAGE"),
                               "COLLECTION", NAME="Tally Connect Collection", ISINITIALIZE="Yes", ISMODIFY="No")
    ET.SubElement(collection, "TYPE").text = view.object_type
    for field in view.fields:
        ET.SubElement(collection, "NATIVEMETHOD").text = field
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
