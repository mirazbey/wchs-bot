"""Exact gates and a polling client for the serialized WhatsApp conversation."""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


UNKNOWN_GATE = "Kapı doğrulanamadı"


def normalize_flight(value):
    return re.sub(r"^([A-Z0-9]{2})0+(?=\d)", r"\1", re.sub(r"\s+", "", str(value)).upper())


def normalize_gate(value):
    gate = re.sub(r"\s+", "", str(value or "")).upper()
    if not re.fullmatch(r"[A-G]\d{1,2}[A-Z]?", gate):
        return None
    # L (Left) = B, R (Right) = A
    if gate.endswith("L"):
        return gate[:-1] + "B"
    elif gate.endswith("R"):
        return gate[:-1] + "A"
    return gate


def exact_gate(value):
    return normalize_gate(value)


def gate_targets(value):
    gate = exact_gate(value)
    if not gate:
        return []
    base = re.match(r"^[A-G]\d{1,2}", gate).group(0)
    if gate == base:
        return [base, base + "A", base + "B", base + "L", base + "R"]
    elif gate.endswith("A"):
        return [gate, base + "R"]
    elif gate.endswith("B"):
        return [gate, base + "L"]
    return [gate]


class GateBridgeClient:
    def __init__(self, url, request_timeout=10, total_timeout=90, poll_interval=1):
        self.url = url.rstrip("/")
        self.request_timeout = request_timeout
        self.total_timeout = total_timeout
        self.poll_interval = poll_interval
        self.cache = {}

    def _request(self, path, payload=None, method=None):
        req = urllib.request.Request(self.url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json", "User-Agent": "WCHS-Bot"}, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=self.request_timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return json.loads(response.read().decode("utf-8"))

    def status(self):
        try:
            return self._request("/status")
        except (OSError, ValueError):
            return {"connected": False, "status": "unreachable"}

    def lookup(self, flight, date=None, direction="arrival", progress=None):
        flight = normalize_flight(flight)
        date = date or (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()
        key = (flight, date, direction)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < cached[2]:
            return dict(cached[1], fromCache=True)
        deadline = time.monotonic() + self.total_timeout
        job_id = None
        previous_status = None
        try:
            result = self._request("/queries", {"flight": flight, "date": date, "direction": direction})
            while result.get("pending"):
                job_id = result.get("jobId")
                if not job_id or not re.fullmatch(r"[a-zA-Z0-9-]+", job_id):
                    return {"success": False, "status": "protocol_error", "gate": None}
                if time.monotonic() >= deadline:
                    self._request("/queries/" + job_id, method="DELETE")
                    return {"success": False, "status": "timeout", "gate": None}
                if progress and result.get("status") != previous_status:
                    previous_status = result.get("status")
                    progress(previous_status)
                time.sleep(self.poll_interval)
                result = self._request("/queries/" + job_id)
            gate = exact_gate(result.get("gate"))
            if result.get("success") and result.get("status") == "confirmed" and gate:
                result["gate"] = gate
                self.cache[key] = (time.monotonic(), result, 1800)
                return result
            # Cache failure / unannounced for 120 seconds to avoid rapid retry
            self.cache[key] = (time.monotonic(), result, 120)
            result["success"] = False
            result["gate"] = None
            return result
        except (OSError, ValueError):
            if job_id:
                try:
                    self._request("/queries/" + job_id, method="DELETE")
                except (OSError, ValueError):
                    pass
            return {"success": False, "status": "unreachable", "gate": None}
