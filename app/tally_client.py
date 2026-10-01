"""Network boundary for the local or office TallyPrime HTTP server."""

import ipaddress
import socket

import httpx

from app.xml_builder import build_collection_request
from app.xml_parser import MAX_RESPONSE_BYTES, TallyError, parse_collection_response


def resolve_private_host(host: str) -> str:
    """Resolve once and connect only to a loopback or private address."""
    host = host.strip()
    if not host or len(host) > 253 or any(c in host for c in "/\\:@?# \t\r\n"):
        raise TallyError("Valid Host/IP Address enter karein.")
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise TallyError("Host/IP Address resolve nahi hua.") from exc
    allowed = [ipaddress.ip_network(value) for value in
               ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")]
    if not addresses or any(not any(ipaddress.ip_address(value) in network for network in allowed)
                            for value in addresses):
        raise TallyError("Local MVP me sirf localhost ya private office-network IP supported hai.")
    return sorted(addresses, key=lambda value: (":" in value, value))[0]


async def fetch_tally(host: str, port: int, view_name: str, company: str = "",
                      from_date=None, to_date=None) -> list:
    address = resolve_private_host(host)
    literal = f"[{address}]" if ":" in address else address
    url = f"http://{literal}:{port}/"
    payload = build_collection_request(view_name, company, from_date, to_date)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=4.0),
                                     trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", url, content=payload,
                                     headers={"Content-Type": "text/xml; charset=utf-8"}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise TallyError("Response bahut bada hai. Chhoti date range try karein.")
                    chunks.append(chunk)
        return parse_collection_response(b"".join(chunks), view_name)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise TallyError(f"{host}:{port} par Tally connect nahi hua. TallyPrime, company aur HTTP Server check karein.") from exc
    except httpx.TimeoutException as exc:
        raise TallyError("Tally ne samay par response nahi diya. Chhoti date range try karein.") from exc
    except httpx.HTTPStatusError as exc:
        raise TallyError(f"Tally endpoint ne HTTP {exc.response.status_code} diya.") from exc
    except httpx.HTTPError as exc:
        raise TallyError(f"Tally request fail hui: {type(exc).__name__}.") from exc
