"""Supported read-only views and their Tally collection methods."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class View:
    title: str
    source: str
    object_type: str
    fields: tuple
    dated: bool = False


VIEWS = {
    "companies": View("Companies", "companies", "Company", ("Name",)),
    "ledgers": View("Ledgers", "ledgers", "Ledger", ("Name", "Parent", "OpeningBalance", "ClosingBalance", "MasterID")),
    "parties": View("Parties", "ledgers", "Ledger", ("Name", "Parent", "ClosingBalance", "MasterID")),
    "stock-items": View("Stock Items", "stock-items", "Stock Item", ("Name", "Parent", "BaseUnits", "ClosingBalance", "ClosingRate", "ClosingValue", "MasterID")),
    "stock-groups": View("Stock Groups", "stock-groups", "Stock Group", ("Name", "Parent", "MasterID")),
    "stock-summary": View("Stock Summary", "stock-items", "Stock Item", ("Name", "Parent", "ClosingBalance", "ClosingRate", "ClosingValue")),
    "vouchers": View("Vouchers", "vouchers", "Voucher", ("Date", "VoucherTypeName", "VoucherNumber", "PartyLedgerName", "Narration", "Amount", "MasterID"), True),
    "sales": View("Sales Transactions", "vouchers", "Voucher", ("Date", "VoucherTypeName", "VoucherNumber", "PartyLedgerName", "Narration", "Amount", "MasterID"), True),
    "purchases": View("Purchase Transactions", "vouchers", "Voucher", ("Date", "VoucherTypeName", "VoucherNumber", "PartyLedgerName", "Narration", "Amount", "MasterID"), True),
    "day-book": View("Day Book", "vouchers", "Voucher", ("Date", "VoucherTypeName", "VoucherNumber", "PartyLedgerName", "Narration", "Amount", "MasterID"), True),
}


def select_rows(view_name: str, rows: list) -> list:
    if view_name in ("stock-items", "stock-summary"):
        for row in rows:
            value = row.get("ClosingValue", "")
            if value:
                try:
                    # Tally's native stock closing value has the opposite sign
                    # from the value shown in its Stock Summary report.
                    row["ClosingValue"] = str(-Decimal(value.replace(",", "")))
                except InvalidOperation:
                    pass
    if view_name == "parties":
        return [row for row in rows if row.get("Parent", "").casefold() in ("sundry debtors", "sundry creditors")]
    if view_name == "sales":
        return [row for row in rows if row.get("VoucherTypeName", "").casefold().startswith("sales")]
    if view_name == "purchases":
        return [row for row in rows if row.get("VoucherTypeName", "").casefold().startswith("purchase")]
    return rows
