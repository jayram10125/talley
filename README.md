# Tally Connect — FastAPI with local and cloud connector modes

This application sends XML export and import requests to TallyPrime's HTTP server. The browser can view data and create, edit, or delete supported records. In local mode, FastAPI runs on the Tally PC or office network. In cloud mode, a small Python connector runs on the Tally PC and polls the cloud over outbound HTTPS. The cloud server never tries to access the customer's `localhost`.

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

## Supported data and CRUD

The dashboard provides companies, ledgers, parties, stock items, stock groups, stock summary, vouchers, sales, purchases, and day book. Parties are ledgers whose direct parent is `Sundry Debtors` or `Sundry Creditors`. Sales and purchases are voucher rows selected by voucher type prefix. Stock summary is an item-wise view of closing quantity, rate, and value; it is not Tally's full configurable Stock Summary report. Tally's native stock closing value sign is inverted to match the positive inventory value shown in its report. Voucher dates are optional and blank by default, so the available Tally period is fetched. Data is fetched live on each refresh. CSV download uses the same filters. Tally may expose different fields depending on version and company configuration; unavailable values appear blank.

Create, Edit and Delete are available in the table for ledgers/parties, stock groups, stock items and vouchers. Sales, Purchases and Day Book edit their underlying vouchers. Stock Summary edits its underlying stock items. Parties edit their underlying ledgers. Companies are selected, not created by this application.

Voucher creation needs at least two signed ledger entries whose total is zero. A debit is negative and a credit is positive in the Tally XML representation. A party line can include a bill reference. Sales/Purchase vouchers may include optional stock lines with quantity, unit, rate, accounting ledger, godown and batch. Voucher Edit can change date, type, number and narration; enable **Replace voucher ledger/stock lines** when changing the financial entries. Each write is sent to the explicitly selected company and is accepted only if Tally reports exactly one record created, altered or deleted and zero errors. Delete requires a browser confirmation. Tally may reject edits or deletes when a record is in use or company rules prevent the action.

**Before using writes on important accounts, back up the Tally company and try them on a test company.** The automated write tests use a fake Tally endpoint and never change a real company. XML field requirements can depend on Tally version and enabled company features. In particular, tax, cost centres, complex bill allocations, and complex invoice modes need additional fields beyond this form; Tally will report an import error when required fields are absent.

The API is documented at `http://127.0.0.1:8000/docs`. Key routes:

- `POST /api/connection/test` with `{"host":"localhost","port":9000}`
- `GET /api/data/companies?host=localhost&port=9000`
- `GET /api/data/ledgers?host=localhost&port=9000&company=Your%20Company`
- `GET /api/data/vouchers?host=localhost&port=9000&company=Your%20Company&from_date=2026-10-01&to_date=2026-10-31`
- `GET /api/export/ledgers.csv?...`
- `POST /api/records/ledgers` to create a ledger
- `PATCH /api/records/ledgers` to alter a ledger by `original_name`
- `DELETE /api/records/ledgers` to delete a ledger by `name`
- The same write routes accept `parties`, `stock-groups`, `stock-items`, and `vouchers`; voucher updates/deletes identify an existing transaction by original date, voucher type and voucher number. All write bodies include `host`, `port`, and `company`. Master create bodies include `name` and `parent`; master updates also include `original_name`. Voucher create bodies include `date`, `voucher_type`, optional `voucher_number`, and balanced `ledger_entries` (`ledger_name`, signed `amount`, optional `is_party`). Voucher updates additionally include `original_date`, `original_type`, and `original_number`.

## Live website: cloud mode

The current cloud mode is a **single-owner live deployment** protected by one administrator login. It supports pairing several Tally PCs to that owner. It is not a multi-customer SaaS account system: customer accounts, tenant isolation, billing, and audit logs need separate development before selling shared access to unrelated customers.

1. Deploy this project on a server with a public HTTPS URL and a **persistent disk**. Configure environment variables:

   ```text
   TALLY_MODE=cloud
   CLOUD_ADMIN_USER=owner
   CLOUD_ADMIN_PASSWORD=<a unique password of at least 20 characters>
   BRIDGE_DB_PATH=/persistent-data/bridge.sqlite3
   ```

2. Start **one** FastAPI worker: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`. Put it behind the host's HTTPS reverse proxy. Keep `BRIDGE_DB_PATH` on durable storage; deleting it invalidates all connector pairings. Do not put the Tally HTTP port on the public internet.
3. Open the HTTPS website and sign in via the browser's Basic Authentication prompt. Click **Pair new PC**. The displayed code expires after 10 minutes.
4. On the computer where TallyPrime is running, install Python and this project, run `pip install -r requirements.txt`, then run the command shown on the website, for example:

   ```powershell
   python -m connector.agent --server https://your-site.example --pair-code YOUR_CODE --tally-port 9001
   ```

   The connector uses `localhost` for Tally by default. For an office-network Tally machine, add `--tally-host 192.168.x.x`. Its credential is saved under the PC user's `~/.tally-connect/connector.json`. Keep that file private. After pairing, restart with the same command **without** `--pair-code`.
5. On the website, click **Refresh PCs**, select the online connector, then **Test Connection** and **Connect Tally**. Choose a loaded company. The data and CRUD screens work through the connector.

The connector must remain running while the website accesses Tally. It connects outward to the website; no inbound firewall rule or public Tally port is required on the PC. The website's administrator password and connector token must remain secret. Protect the persistent database and keep regular backups. A request waits up to 40 seconds. If a write times out after the connector has started it, check its job status and the Tally record before retrying to avoid duplicates. Cloud deployment has not been tested against the user's real hosted URL because that URL and hosting details have not yet been provided.

To disconnect a PC permanently, call `DELETE /api/bridge/connectors/{connector_id}` with the administrator login. This revokes its saved token. The database removes expired pairing codes and jobs older than 24 hours when new pairing codes or jobs are created.

## Structure and deployment boundary

`app/xml_builder.py` builds XML export requests, `app/xml_import.py` builds import requests and checks counters, `app/write_models.py` validates write input, `app/tally_client.py` handles HTTP and private-network host validation, `app/xml_parser.py` normalizes XML responses, `app/catalog.py` defines supported views, `app/main.py` contains FastAPI routes, and `app/static/` contains the UI.

Local mode has no user login and must remain bound to localhost or a trusted office network. Cloud mode stores pairing credentials and short-lived request/response jobs in SQLite; it does not permanently synchronize Tally data. The connector is in `connector/agent.py`, and its queue is in `app/bridge.py`. For a multi-customer commercial deployment, add separate customer authentication and authorization, tenant data isolation, audit logs, and managed connector distribution.

The response size is limited to 12 MB and calls time out after 25 seconds. For very large datasets, narrow the date range; a future connector can add cursor-based synchronization. An actual TallyPrime instance is required to validate version-specific collection output.

## Tests

Run `python -m unittest discover -s tests -v`.
