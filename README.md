# Tally Connect — local FastAPI MVP

This is a read-only local web application for TallyPrime. It sends XML collection export requests to TallyPrime's HTTP server, converts responses to JSON, and displays the results in a browser. Host/IP and Port are entered by the user; defaults are `localhost` and `9000`.

## Run on the Tally computer

1. Install Python 3.9 or newer.
2. In TallyPrime, load at least one company and enable the HTTP Server under **F1 (Help) → Settings → Advanced Configuration**. Note its port (usually `9000`).
3. In this project directory, run:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

4. Open `http://127.0.0.1:8000`. Enter the Tally Host/IP and Port, click **Test Connection**, then **Connect Tally**. Select a loaded company and use the navigation to fetch records.

During development, `--reload` restarts FastAPI whenever Python files change. If an older server is already using port `8000`, stop it before running this command; a browser refresh alone does not reload Python code in a running server.

If Tally runs on another office computer, use its private network IP and enable inbound access to its configured Tally HTTP port in that computer's firewall. Keep the FastAPI UI bound to `127.0.0.1` on the user's PC for this MVP.

## Supported data

The dashboard provides companies, ledgers, parties, stock items, stock groups, stock summary, vouchers, sales, purchases, and day book. Parties are ledgers whose direct parent is `Sundry Debtors` or `Sundry Creditors`. Sales and purchases are voucher rows selected by voucher type prefix. Stock summary is an item-wise view of closing quantity, rate, and value; it is not Tally's full configurable Stock Summary report. Tally's native stock closing value sign is inverted to match the positive inventory value shown in its report. Voucher dates are optional and blank by default, so the available Tally period is fetched. Data is fetched live on each refresh. CSV download uses the same filters. Tally may expose different fields depending on version and company configuration; unavailable values appear blank.

The API is documented at `http://127.0.0.1:8000/docs`. Key routes:

- `POST /api/connection/test` with `{"host":"localhost","port":9000}`
- `GET /api/data/companies?host=localhost&port=9000`
- `GET /api/data/ledgers?host=localhost&port=9000&company=Your%20Company`
- `GET /api/data/vouchers?host=localhost&port=9000&company=Your%20Company&from_date=2026-10-01&to_date=2026-10-31`
- `GET /api/export/ledgers.csv?...`

## Structure and deployment boundary

`app/xml_builder.py` builds XML requests, `app/tally_client.py` handles HTTP and private-network host validation, `app/xml_parser.py` normalizes XML responses, `app/catalog.py` defines supported views, `app/main.py` contains FastAPI routes, and `app/static/` contains the UI.

This local MVP has no user accounts or remote access and should not be exposed directly to the internet. It does not persist Tally data. For a multi-customer cloud deployment, add tenant authentication and authorization, an outbound HTTPS local connector running on each customer's Tally machine, customer-specific connector credentials, encrypted transport, a job/polling channel, audit logs, and per-tenant data isolation. The cloud backend cannot directly reach a customer's `localhost`; that connector must make outbound connections and relay approved read-only requests/results. These cloud components are future architecture, not part of this local MVP.

The response size is limited to 12 MB and calls time out after 25 seconds. For very large datasets, narrow the date range; a future connector can add cursor-based synchronization. An actual TallyPrime instance is required to validate version-specific collection output.

## Tests

Run `python -m unittest discover -s tests -v`.
