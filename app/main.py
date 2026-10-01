"""Local Tally data explorer."""

import csv
from datetime import date
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.catalog import VIEWS, select_rows
from app.tally_client import fetch_tally
from app.xml_parser import TallyError


app = FastAPI(title="Tally Connect", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class Connection(BaseModel):
    host: str = Field(default="localhost", min_length=1, max_length=253)
    port: int = Field(default=9000, ge=1, le=65535)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def script():
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript")


@app.post("/api/connection/test")
async def test_connection(connection: Connection):
    try:
        companies = await fetch_tally(connection.host, connection.port, "companies")
    except TallyError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"connected": True, "host": connection.host, "port": connection.port,
            "companies": companies, "message": "TallyPrime connected"}


@app.get("/api/views")
def views():
    return {key: {"title": view.title, "fields": view.fields, "dated": view.dated}
            for key, view in VIEWS.items() if key != "companies"}


@app.get("/api/data/{view_name}")
async def get_data(view_name: str, host: str = Query("localhost", max_length=253),
                   port: int = Query(9000, ge=1, le=65535),
                   company: str = Query("", max_length=200),
                   from_date: date = None, to_date: date = None):
    if view_name not in VIEWS:
        raise HTTPException(404, "Unknown data section")
    if view_name != "companies" and not company.strip():
        raise HTTPException(400, "Company select karein.")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "From date, to date se pehle honi chahiye.")
    try:
        rows = await fetch_tally(host, port, view_name, company, from_date, to_date)
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
                     from_date: date = None, to_date: date = None):
    result = await get_data(view_name, host, port, company, from_date, to_date)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=VIEWS[view_name].fields)
    writer.writeheader()
    for row in result["rows"]:
        writer.writerow({key: "'" + value if value.startswith(("=", "+", "-", "@")) else value
                         for key, value in row.items()})
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="tally-{view_name}.csv"'})
