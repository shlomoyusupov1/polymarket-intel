"""HTTP helper with retries, exponential backoff and Retry-After support."""
import logging
import time

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiClient:
    def __init__(self, base: str, client: httpx.Client | None = None, max_retries: int = 5,
                 backoff: float = 1.0, sleep=time.sleep, min_interval: float = 0.1):
        self.base = base.rstrip("/")
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": "pmintel/0.1"})
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep
        self.min_interval = min_interval  # polite pacing between requests
        self._last = 0.0

    def get(self, path: str, params: dict | None = None):
        url = f"{self.base}{path}"
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise
                delay = self.backoff * 2 ** attempt
                log.warning("transport error %s on %s, retry in %.1fs", e, url, delay)
                self.sleep(delay)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                ra = r.headers.get("Retry-After")
                delay = self.backoff * 2 ** attempt
                if ra and ra.replace(".", "", 1).isdigit():
                    delay = max(delay, float(ra))  # "Retry-After: 0" must not mean hammering
                log.warning("HTTP %s on %s, retry in %.1fs", r.status_code, url, delay)
                self.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")
