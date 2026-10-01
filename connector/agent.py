"""Run on the Tally PC: python -m connector.agent --server URL --pair-code CODE --tally-port 9001"""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from app.tally_client import send_xml
from app.xml_parser import TallyError


def validate_server(url):
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Cloud server ka root URL dein, extra path ya credentials nahi.")
    if parsed.scheme == "https" and parsed.hostname:
        return url.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url.rstrip("/")
    raise ValueError("Cloud server URL HTTPS hona chahiye (local testing me http://localhost allowed hai).")


def validate_job(xml):
    try:
        root = ET.fromstring(xml)
        request = root.findtext("./HEADER/TALLYREQUEST")
        if root.tag != "ENVELOPE" or request not in ("Export", "Import"):
            raise ValueError("Unsupported Tally request")
    except ET.ParseError as exc:
        raise ValueError("Invalid Tally XML request") from exc
    return xml.encode("utf-8")


async def run(server, pair_code, tally_host, tally_port, config_path, on_status=None, stop_event=None):
    def report(state, detail):
        if on_status:
            on_status(state, detail)
        else:
            print(detail)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    server = validate_server(server)
    path = Path(config_path).expanduser()
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if saved and saved.get("server") != server:
        raise ValueError("Saved connector kisi aur server se paired hai. Naya --config path use karein.")
    tally_host = tally_host or saved.get("tally_host") or "localhost"
    tally_port = tally_port or saved.get("tally_port") or 9000
    if not 1 <= tally_port <= 65535:
        raise ValueError("Valid Tally port 1 se 65535 dein.")
    timeout = httpx.Timeout(35.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        if pair_code:
            response = await client.post(server + "/api/bridge/register", json={"code": pair_code})
            response.raise_for_status()
            registration = response.json()
            tally_port = registration.get("tally_port") or tally_port
            saved = {"server": server, "tally_host": tally_host, "tally_port": tally_port, **registration}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved), encoding="utf-8")
            temporary.replace(path)
            if os.name != "nt":
                path.chmod(0o600)
            report("paired", "Paired connector: " + saved["connector_id"])
        if not saved.get("token"):
            raise ValueError("Pehli baar --pair-code zaroori hai.")
        headers = {"Authorization": "Bearer " + saved["token"]}
        started = False
        while not stopped():
            try:
                response = await client.get(server + "/api/bridge/agent/jobs", headers=headers)
                response.raise_for_status()
                if not started:
                    report("online", f"Connector running. Cloud pairing verified. Tally endpoint: {tally_host}:{tally_port}")
                    started = True
                job = response.json()
                if not job:
                    await asyncio.sleep(1)
                    continue
                try:
                    payload = validate_job(job["xml"])
                    result = await send_xml(tally_host, tally_port, payload)
                    data = {"xml_base64": base64.b64encode(result).decode("ascii")}
                except (TallyError, ValueError, UnicodeError) as exc:
                    data = {"error": str(exc)}
                while True:
                    try:
                        finished = await client.post(server + "/api/bridge/agent/jobs/" + job["id"],
                                                     headers=headers, json=data)
                        finished.raise_for_status()
                        break
                    except httpx.HTTPError as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403, 404):
                            raise RuntimeError(f"Cloud ne job {job['id']} result reject kiya: HTTP {exc.response.status_code}. Tally me record verify karein.") from exc
                        report("warning", "Result delivery failed for job " + job["id"] + ": " + str(exc))
                        await asyncio.sleep(5)
                report("job", "Job " + job["id"] + (" complete" if "xml_base64" in data else ": " + data["error"]))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    raise RuntimeError("Cloud pairing invalid hai. Website par Pair new PC se naya code lekar connector dobara chalayein.") from exc
                started = False
                report("warning", "Connector issue: " + str(exc))
                await asyncio.sleep(5)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                started = False
                report("warning", "Connector issue: " + str(exc))
                await asyncio.sleep(5)
        report("stopped", "Connector stopped.")


def main():
    parser = argparse.ArgumentParser(description="Outbound TallyPrime connector")
    parser.add_argument("--server", required=True, help="Cloud website HTTPS URL")
    parser.add_argument("--pair-code", default="", help="One-time code shown on website")
    parser.add_argument("--tally-host", default=None)
    parser.add_argument("--tally-port", type=int, default=None)
    parser.add_argument("--config", default=str(Path.home() / ".tally-connect" / "connector.json"))
    args = parser.parse_args()
    try:
        asyncio.run(run(args.server, args.pair_code, args.tally_host, args.tally_port, args.config))
    except KeyboardInterrupt:
        print("Connector stopped.")


if __name__ == "__main__":
    main()
