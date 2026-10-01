"""Durable, single-worker queue for an outbound Tally connector."""

import asyncio
import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from app.xml_parser import MAX_RESPONSE_BYTES, TallyError


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class BridgeStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS connectors (
                    id TEXT PRIMARY KEY, token_hash TEXT, created_at REAL NOT NULL,
                    last_seen REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pair_codes (
                    code_hash TEXT PRIMARY KEY, connector_id TEXT NOT NULL,
                    expires_at REAL NOT NULL, tally_port INTEGER);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, connector_id TEXT NOT NULL,
                    payload BLOB NOT NULL, status TEXT NOT NULL,
                    result BLOB, error TEXT, created_at REAL NOT NULL,
                    claimed_at REAL, finished_at REAL);
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(connector_id,status,created_at);
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(pair_codes)")}
            if "tally_port" not in columns:
                self.db.execute("ALTER TABLE pair_codes ADD COLUMN tally_port INTEGER")

    def close(self):
        with self.lock:
            self.db.close()

    def pair_code(self, tally_port=None):
        if tally_port is not None and (type(tally_port) is not int or not 1 <= tally_port <= 65535):
            raise ValueError("Invalid Tally port")
        code = secrets.token_urlsafe(18)
        connector_id = uuid.uuid4().hex
        with self.lock, self.db:
            self._prune()
            self.db.execute("INSERT INTO connectors(id,created_at) VALUES (?,?)", (connector_id, time.time()))
            self.db.execute("INSERT INTO pair_codes(code_hash,connector_id,expires_at,tally_port) VALUES (?,?,?,?)",
                            (digest(code), connector_id, time.time() + 600, tally_port))
        return {"code": code, "connector_id": connector_id, "expires_in": 600}

    def register(self, code):
        with self.lock, self.db:
            row = self.db.execute("SELECT connector_id,tally_port FROM pair_codes WHERE code_hash=? AND expires_at>?",
                                  (digest(code), time.time())).fetchone()
            if not row:
                return None
            self.db.execute("DELETE FROM pair_codes WHERE code_hash=?", (digest(code),))
            token = secrets.token_urlsafe(48)
            self.db.execute("UPDATE connectors SET token_hash=?,last_seen=? WHERE id=?",
                            (digest(token), time.time(), row["connector_id"]))
            result = {"connector_id": row["connector_id"], "token": token}
            if row["tally_port"] is not None:
                result["tally_port"] = row["tally_port"]
            return result

    def authenticate(self, token):
        if not token:
            return None
        with self.lock, self.db:
            row = self.db.execute("SELECT id FROM connectors WHERE token_hash=?", (digest(token),)).fetchone()
            if row:
                self.db.execute("UPDATE connectors SET last_seen=? WHERE id=?", (time.time(), row["id"]))
                return row["id"]
        return None

    def connectors(self):
        with self.lock:
            rows = self.db.execute("SELECT id,created_at,last_seen,token_hash FROM connectors ORDER BY created_at DESC").fetchall()
        return [{"id": row["id"], "paired": bool(row["token_hash"]),
                 "online": bool(row["token_hash"] and time.time() - row["last_seen"] < 15),
                 "last_seen": row["last_seen"]} for row in rows]

    def revoke(self, connector_id):
        with self.lock, self.db:
            self.db.execute("DELETE FROM pair_codes WHERE connector_id=?", (connector_id,))
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE connector_id=? AND status='pending'", (connector_id,))
            return bool(self.db.execute("DELETE FROM connectors WHERE id=?", (connector_id,)).rowcount)

    def _prune(self):
        self.db.execute("DELETE FROM pair_codes WHERE expires_at<?", (time.time(),))
        self.db.execute("DELETE FROM jobs WHERE created_at<?", (time.time() - 86400,))

    def is_online(self, connector_id):
        return any(row["id"] == connector_id and row["online"] for row in self.connectors())

    def enqueue(self, connector_id, payload):
        if len(payload) > MAX_RESPONSE_BYTES:
            raise TallyError("Tally request bahut badi hai.")
        if not self.is_online(connector_id):
            raise TallyError("Connector offline hai. Tally PC par connector chalu karein.")
        job_id = uuid.uuid4().hex
        with self.lock, self.db:
            self._prune()
            self.db.execute("INSERT INTO jobs(id,connector_id,payload,status,created_at) VALUES (?,?,?,?,?)",
                            (job_id, connector_id, payload, "pending", time.time()))
        return job_id

    def claim(self, connector_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT id,payload FROM jobs WHERE connector_id=? AND status='pending' ORDER BY created_at LIMIT 1",
                                  (connector_id,)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE jobs SET status='running',claimed_at=? WHERE id=?", (time.time(), row["id"]))
            return {"id": row["id"], "xml": row["payload"].decode("utf-8")}

    def finish(self, connector_id, job_id, result=None, error=None):
        if result is not None and len(result) > MAX_RESPONSE_BYTES:
            raise TallyError("Tally response bahut badi hai.")
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE jobs SET status=?,result=?,error=?,finished_at=? WHERE id=? AND connector_id=? AND status='running'",
                                     ("failed" if error else "done", result, error, time.time(), job_id, connector_id))
            return bool(cursor.rowcount)

    def status(self, job_id):
        with self.lock:
            return self.db.execute("SELECT status,result,error FROM jobs WHERE id=?", (job_id,)).fetchone()

    def cancel_pending(self, job_id):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE id=? AND status='pending'", (job_id,))

    async def dispatch(self, connector_id, payload):
        job_id = self.enqueue(connector_id, payload)
        for _ in range(160):
            row = self.status(job_id)
            if row["status"] == "done":
                return row["result"]
            if row["status"] == "failed":
                raise TallyError(row["error"] or "Connector se Tally request fail hui.")
            await asyncio.sleep(0.25)
        self.cancel_pending(job_id)
        raise TallyError(f"Connector response timeout. Job {job_id} ka status check karein; write ko bina check kiye retry na karein.")
