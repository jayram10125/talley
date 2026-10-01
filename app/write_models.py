"""Validated input for Tally writes. No arbitrary XML is accepted from the browser."""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, root_validator, validator


class TallyTarget(BaseModel):
    host: str = Field("localhost", min_length=1, max_length=253)
    port: int = Field(9000, ge=1, le=65535)
    connector_id: str = Field("", max_length=64)
    company: str = Field(..., min_length=1, max_length=200)

    @validator("company")
    def nonblank_company(cls, value):
        if not value.strip():
            raise ValueError("Company select karein.")
        return value.strip()


class MasterWrite(TallyTarget):
    name: str = Field(..., min_length=1, max_length=200)
    parent: Optional[str] = Field(None, max_length=200)
    base_units: Optional[str] = Field(None, max_length=50)
    opening_balance: Optional[str] = Field(None, max_length=80)
    opening_quantity: Optional[str] = Field(None, max_length=80)
    opening_rate: Optional[str] = Field(None, max_length=80)
    opening_value: Optional[str] = Field(None, max_length=80)

    @validator("name")
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Name required hai.")
        return value.strip()


class MasterUpdate(MasterWrite):
    original_name: str = Field(..., min_length=1, max_length=200)


class MasterDelete(TallyTarget):
    name: str = Field(..., min_length=1, max_length=200)


class LedgerEntry(BaseModel):
    ledger_name: str = Field(..., min_length=1, max_length=200)
    amount: Decimal
    is_party: bool = False
    bill_reference: Optional[str] = Field(None, max_length=100)
    bill_type: Optional[str] = Field(None, max_length=30)

    @validator("amount")
    def nonzero_amount(cls, value):
        if not value.is_finite() or value == 0:
            raise ValueError("Ledger amount zero nahi ho sakta.")
        return value

    @root_validator(skip_on_failure=True)
    def bill_requires_party(cls, values):
        if values.get("bill_reference") and not values.get("is_party"):
            raise ValueError("Bill reference sirf Party ledger line ke saath den.")
        return values


class InventoryEntry(BaseModel):
    stock_item: str = Field(..., min_length=1, max_length=200)
    quantity: Decimal = Field(..., gt=0)
    unit: str = Field(..., min_length=1, max_length=50)
    rate: Decimal = Field(..., ge=0)
    ledger_name: str = Field(..., min_length=1, max_length=200)
    godown: Optional[str] = Field(None, max_length=200)
    batch_name: Optional[str] = Field(None, max_length=200)

    @validator("quantity", "rate")
    def finite_decimal(cls, value):
        if not value.is_finite():
            raise ValueError("Finite number required hai.")
        return value


class VoucherWrite(TallyTarget):
    date: date
    voucher_type: str = Field(..., min_length=1, max_length=100)
    voucher_number: Optional[str] = Field(None, max_length=100)
    narration: str = Field("", max_length=1000)
    ledger_entries: List[LedgerEntry] = Field(..., min_items=2)
    inventory_entries: List[InventoryEntry] = Field(default_factory=list)

    @root_validator(skip_on_failure=True)
    def balanced(cls, values):
        entries = values.get("ledger_entries")
        if entries is not None and sum((entry.amount for entry in entries), Decimal(0)) != 0:
            raise ValueError("Voucher ledger debit aur credit ka total equal hona chahiye.")
        if values.get("inventory_entries") and not values.get("voucher_type", "").casefold().startswith(("sales", "purchase")):
            raise ValueError("Inventory lines sirf Sales ya Purchase voucher me supported hain.")
        return values


class VoucherUpdate(VoucherWrite):
    ledger_entries: Optional[List[LedgerEntry]] = Field(None, min_items=2)
    inventory_entries: Optional[List[InventoryEntry]] = None
    original_date: date
    original_type: str = Field(..., min_length=1, max_length=100)
    original_number: str = Field(..., min_length=1, max_length=100)


class VoucherDelete(TallyTarget):
    date: date
    voucher_type: str = Field(..., min_length=1, max_length=100)
    voucher_number: str = Field(..., min_length=1, max_length=100)
