"""Optional: send each gateway savings record to Langfuse as a trace.

Off by default. Keys come only from the environment (LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY); they are never read from the YAML or the dashboard. Enabled but missing keys
means a no-op with one warning. Sends happen on a background thread; a failure never touches the
request path.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone

ENV = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def trace_event(rec: dict) -> dict:
    """One Langfuse ingestion ``trace-create`` event for a savings record (no prompt text)."""
    ts = datetime.fromtimestamp(rec.get("ts", 0), tz=timezone.utc).isoformat()
    return {"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": ts,
            "body": {"id": rec["receipt_id"], "name": "wattnot-gateway", "timestamp": ts,
                     "sessionId": rec.get("run") or None,
                     "metadata": {"receipt_id": rec["receipt_id"], "prev_receipt_id": rec.get("prev_receipt_id"),
                                  "actual": rec["actual"], "baseline": rec["baseline"],
                                  "labels": rec["labels"], "stepped_up": rec["stepped_up"],
                                  "guard_stopped": rec["guard_stopped"]},
                     "tags": ["wattnot", rec["labels"]["joules"]]}}


class LangfuseSink:
    def __init__(self, host: str | None = None, pk: str | None = None, sk: str | None = None,
                 opener=None) -> None:
        self.host, self.pk, self.sk = (host or "").rstrip("/"), pk or "", sk or ""
        self.opener = opener or urllib.request.urlopen
        self.sent = 0
        self.failed = 0

    @property
    def active(self) -> bool:
        return bool(self.host and self.pk and self.sk)

    @classmethod
    def from_config(cls, cfg: dict, opener=None) -> "LangfuseSink":
        if not ((cfg.get("integrations") or {}).get("langfuse") or {}).get("enabled"):
            return cls(opener=opener)
        vals = [os.environ.get(k, "").strip() for k in ENV]
        if not all(vals):
            print("wattnot gateway: langfuse enabled but LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY not set; "
                  "traces off", file=sys.stderr)
            return cls(opener=opener)
        return cls(*vals, opener=opener)

    def _post(self, rec: dict) -> None:
        auth = base64.b64encode(f"{self.pk}:{self.sk}".encode()).decode()
        req = urllib.request.Request(self.host + "/api/public/ingestion",
                                     data=json.dumps({"batch": [trace_event(rec)]}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
        try:
            with self.opener(req, timeout=10) as r:  # noqa: S310
                r.read()
            self.sent += 1
        except Exception:  # noqa: BLE001  never break the request path
            self.failed += 1

    def send(self, rec: dict, wait: bool = False) -> None:
        if not self.active:
            return
        t = threading.Thread(target=self._post, args=(rec,), daemon=True)
        t.start()
        if wait:
            t.join()
