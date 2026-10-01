"""Local Tally data explorer."""

import csv
import base64
import os
import secrets
from datetime import date
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.catalog import VIEWS, select_rows
from app.bridge import BridgeStore
from app.tally_client import fetch_tally, import_tally, send_xml
from app.xml_builder import build_collection_request
from app.xml_parser import parse_collection_response, MAX_RESPONSE_BYTES
from app.xml_import import parse_import_result
from app.xml_import import MASTER_TAGS, build_master_import, build_voucher_import
from app.write_models import MasterDelete, MasterUpdate, MasterWrite, VoucherDelete, VoucherUpdate, VoucherWrite
from app.xml_parser import TallyError


app = FastAPI(title="Tally Connect", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONNECTOR_BINARY = Path(__file__).resolve().parent / "downloads" / "TallyConnector.exe"
CLOUD_MODE = os.getenv("TALLY_MODE", "local").lower() == "cloud"
BRIDGE = BridgeStore(os.getenv("BRIDGE_DB_PATH", "data/bridge.sqlite3")) if CLOUD_MODE else None
ADMIN_USER = os.getenv("CLOUD_ADMIN_USER", "")
ADMIN_PASSWORD = os.getenv("CLOUD_ADMIN_PASSWORD", "")
if CLOUD_MODE and (not ADMIN_USER or len(ADMIN_PASSWORD) < 20):
    raise RuntimeError("Cloud mode requires CLOUD_ADMIN_USER and CLOUD_ADMIN_PASSWORD (20+ characters).")


@app.middleware("http")
async def cloud_auth(request: Request, call_next):
    if CLOUD_MODE and request.url.path not in ("/health", "/api/bridge/register") and not request.url.path.startswith("/api/bridge/agent/"):
        header = request.headers.get("authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            username, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeError):
            scheme, username, password = "", "", ""
        if scheme.lower() != "basic" or not (secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD)):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Tally Connect"'})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return Response(status_code=403)
    return await call_next(request)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    external_download = os.getenv("CONNECTOR_DOWNLOAD_URL", "")
    return {"mode": "cloud" if CLOUD_MODE else "local",
            "connector_download_url": ("/downloads/TallyConnector.exe" if CONNECTOR_BINARY.is_file()
                                       else external_download if external_download.startswith("https://") else ""),
            "deployment_warning": "Render par TALLY_MODE=cloud configure karein." if os.getenv("RENDER") == "true" and not CLOUD_MODE else None}


@app.get("/downloads/TallyConnector.exe", include_in_schema=False)
def download_connector():
    if CONNECTOR_BINARY.is_file():
        return FileResponse(CONNECTOR_BINARY, media_type="application/octet-stream",
                            filename="TallyConnector.exe", headers={"Cache-Control": "no-store"})
    external = os.getenv("CONNECTOR_DOWNLOAD_URL", "")
    if external.startswith("https://"):
        return RedirectResponse(external)
    raise HTTPException(404, "Connector download is not published")


def agent_id(request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    connector_id = BRIDGE.authenticate(token) if BRIDGE else None
    if not connector_id:
        raise HTTPException(401, "Invalid connector token")
    return connector_id


@app.post("/api/bridge/pair-codes")
async def pair_code(request: Request):
    if not CLOUD_MODE:
        raise HTTPException(404)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    port = body.get("tally_port") if isinstance(body, dict) else None
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise HTTPException(422, "Valid Tally port 1 se 65535 dein.")
    return BRIDGE.pair_code(port)


@app.get("/api/bridge/connectors")
def connectors():
    if not CLOUD_MODE:
        raise HTTPException(404)
    return JSONResponse(BRIDGE.connectors(), headers={"Cache-Control": "no-store"})


@app.delete("/api/bridge/connectors/{connector_id}")
def revoke_connector(connector_id: str):
    if not CLOUD_MODE:
        raise HTTPException(404)
    if not BRIDGE.revoke(connector_id):
        raise HTTPException(404, "Connector not found")
    return {"revoked": True}


@app.post("/api/bridge/register")
async def register(request: Request):
    if not CLOUD_MODE:
        raise HTTPException(404)
    body = await request.json()
    result = BRIDGE.register(str(body.get("code", "")))
    if not result:
        raise HTTPException(401, "Pairing code invalid or expired")
    return result


@app.get("/api/bridge/agent/jobs")
def next_job(request: Request):
    return BRIDGE.claim(agent_id(request))


@app.post("/api/bridge/agent/jobs/{job_id}")
async def finish_job(job_id: str, request: Request):
    connector_id = agent_id(request)
    if int(request.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES * 2:
        raise HTTPException(413, "Response too large")
    body = await request.json()
    result = body.get("xml_base64")
    error = body.get("error")
    if not isinstance(result, str) and not isinstance(error, str):
        raise HTTPException(422, "XML or error required")
    try:
        decoded = base64.b64decode(result, validate=True) if isinstance(result, str) else None
        accepted = BRIDGE.finish(connector_id, job_id, decoded,
                                 error[:500] if isinstance(error, str) else None)
    except (TallyError, ValueError) as exc:
        raise HTTPException(413, str(exc)) from exc
    if not accepted:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


@app.get("/api/bridge/jobs/{job_id}")
def job_status(job_id: str):
    if not CLOUD_MODE:
        raise HTTPException(404)
    row = BRIDGE.status(job_id)
    if not row:
        raise HTTPException(404, "Job not found")
    return {"status": row["status"], "error": row["error"]}


async def tally_fetch(host, port, view_name, company="", from_date=None, to_date=None, connector_id=""):
    if not CLOUD_MODE:
        if from_date is None and to_date is None:
            return await fetch_tally(host, port, view_name, company)
        return await fetch_tally(host, port, view_name, company, from_date, to_date)
    if not connector_id:
        raise TallyError("Connector select karein.")
    payload = build_collection_request(view_name, company, from_date, to_date)
    return parse_collection_response(await BRIDGE.dispatch(connector_id, payload), view_name)


async def tally_import(host, port, payload, action, connector_id=""):
    if not CLOUD_MODE:
        return await import_tally(host, port, payload, action)
    if not connector_id:
        raise TallyError("Connector select karein.")
    return parse_import_result(await BRIDGE.dispatch(connector_id, payload), action)


class Connection(BaseModel):
    host: str = Field(default="localhost", min_length=1, max_length=253)
    port: int = Field(default=9000, ge=1, le=65535)
    connector_id: str = Field(default="", max_length=64)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/app.js", include_in_schema=False)
def script():
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/crud.js", include_in_schema=False)
def crud_script():
    return FileResponse(STATIC_DIR / "crud.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.post("/api/connection/test")
async def test_connection(connection: Connection):
    try:
        companies = await tally_fetch(connection.host, connection.port, "companies", connector_id=connection.connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"connected": True, "host": connection.host, "port": connection.port,
            "connector_id": connection.connector_id,
            "companies": companies, "message": "TallyPrime connected"}


@app.get("/api/views")
def views():
    return {key: {"title": view.title, "fields": view.fields, "dated": view.dated}
            for key, view in VIEWS.items() if key != "companies"}


@app.get("/api/data/{view_name}")
async def get_data(view_name: str, host: str = Query("localhost", max_length=253),
                   port: int = Query(9000, ge=1, le=65535),
                   company: str = Query("", max_length=200),
                   from_date: date = None, to_date: date = None,
                   connector_id: str = Query("", max_length=64)):
    if view_name not in VIEWS:
        raise HTTPException(404, "Unknown data section")
    if view_name != "companies" and not company.strip():
        raise HTTPException(400, "Company select karein.")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "From date, to date se pehle honi chahiye.")
    try:
        rows = await tally_fetch(host, port, view_name, company, from_date, to_date, connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
    rows = select_rows(view_name, rows)
    if VIEWS[view_name].dated and (from_date or to_date):
        start = from_date.strftime("%Y%m%d") if from_date else "00000000"
        end = to_date.strftime("%Y%m%d") if to_date else "99999999"
        rows = [row for row in rows if start <= row.get("Date", "") <= end]
    return {"view": view_name, "company": company, "count": len(rows), "rows": rows}


@app.get("/api/export/{view_name}.csv")
async def export_csv(view_name: str, host: str = Query("localhost", max_length=253),
                     port: int = Query(9000, ge=1, le=65535),
                     company: str = Query("", max_length=200),
                     from_date: date = None, to_date: date = None,
                     connector_id: str = Query("", max_length=64)):
    result = await get_data(view_name, host, port, company, from_date, to_date, connector_id)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=VIEWS[view_name].fields)
    writer.writeheader()
    for row in result["rows"]:
        writer.writerow({key: "'" + value if value.startswith(("=", "+", "-", "@")) else value
                         for key, value in row.items()})
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="tally-{view_name}.csv"'})


async def _check_company(host: str, port: int, company: str, connector_id: str = ""):
    try:
        companies = await tally_fetch(host, port, "companies", connector_id=connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
    if company not in {row.get("Name") for row in companies}:
        raise HTTPException(400, "Selected company Tally me loaded nahi hai. Reconnect karein.")


async def _check_existing(entity: str, data, name: str = ""):
    view = "vouchers" if entity == "vouchers" else "ledgers" if entity == "parties" else entity
    try:
        rows = await tally_fetch(data.host, data.port, view, data.company, connector_id=data.connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
    if entity == "vouchers":
        target_date = (data.original_date if hasattr(data, "original_date") else data.date).strftime("%Y%m%d")
        target_type = data.original_type if hasattr(data, "original_type") else data.voucher_type
        target_number = data.original_number if hasattr(data, "original_number") else data.voucher_number
        return any(row.get("Date") == target_date and row.get("VoucherTypeName") == target_type
                   and row.get("VoucherNumber") == target_number for row in rows)
    return any(row.get("Name") == name for row in rows)


@app.post("/api/records/{entity}")
async def create_record(entity: str, data: dict):
    if entity not in MASTER_TAGS and entity != "vouchers":
        raise HTTPException(404, "This section has no direct create operation")
    from pydantic import ValidationError
    try:
        model = VoucherWrite(**data) if entity == "vouchers" else MasterWrite(**data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _check_company(model.host, model.port, model.company, model.connector_id)
    if entity in ("ledgers", "parties") and not model.parent:
        raise HTTPException(422, "Ledger group/parent required hai.")
    if entity != "vouchers" and await _check_existing(entity, model, model.name):
        raise HTTPException(409, "Is naam ka record pehle se Tally me hai.")
    payload = build_voucher_import("Create", model) if entity == "vouchers" else build_master_import(entity, "Create", model)
    try:
        return await tally_import(model.host, model.port, payload, "Create", model.connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.patch("/api/records/{entity}")
async def update_record(entity: str, data: dict):
    if entity not in MASTER_TAGS and entity != "vouchers":
        raise HTTPException(404, "This section has no direct update operation")
    from pydantic import ValidationError
    try:
        model = VoucherUpdate(**data) if entity == "vouchers" else MasterUpdate(**data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _check_company(model.host, model.port, model.company, model.connector_id)
    if not await _check_existing(entity, model, getattr(model, "original_name", "")):
        raise HTTPException(404, "Original record Tally me nahi mila. Refresh karein.")
    payload = build_voucher_import("Alter", model) if entity == "vouchers" else build_master_import(entity, "Alter", model)
    try:
        return await tally_import(model.host, model.port, payload, "Alter", model.connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/records/{entity}")
async def delete_record(entity: str, data: dict):
    if entity not in MASTER_TAGS and entity != "vouchers":
        raise HTTPException(404, "This section has no direct delete operation")
    from pydantic import ValidationError
    try:
        model = VoucherDelete(**data) if entity == "vouchers" else MasterDelete(**data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _check_company(model.host, model.port, model.company, model.connector_id)
    if not await _check_existing(entity, model, getattr(model, "name", "")):
        raise HTTPException(404, "Record Tally me nahi mila. Refresh karein.")
    payload = build_voucher_import("Delete", model) if entity == "vouchers" else build_master_import(entity, "Delete", model)
    try:
        return await tally_import(model.host, model.port, payload, "Delete", model.connector_id)
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
