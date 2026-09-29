"""Common helpers: configuration, HTTP access and paths.

The scripts use only the Python standard library. They run on Windows,
Linux and macOS without extra packages.
"""
from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"

PLK_BASE = "https://pdp-api.plk-sa.pl/api/v1"
SPOZNIENIA_BASE = "https://api.spoznienia.me"
STATION_NAME = "Iława Główna"
USER_AGENT = "ilawa-transport/1.0 (transfer study)"


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env into os.environ. Existing values stay."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def plk_key() -> str:
    load_env()
    key = os.environ.get("PLK_API_KEY", "").strip()
    if not key:
        raise SystemExit("PLK_API_KEY is missing. Put it in .env or in the environment.")
    return key


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 90, retries: int = 3) -> tuple[int, bytes, dict]:
    """GET a URL. Return (status, body, headers). Retry on network errors, 429 and 5xx."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as e:
            body = e.read() or b""
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After", "0") or 0) or 10 * (attempt + 1)
                time.sleep(wait)
                continue
            return e.code, body, dict(e.headers or {})
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url.split('?')[0]}: {last_err}")


def plk_get(path: str, params: dict | None = None, timeout: int = 120):
    """Call the PKP PLK open data API. The key goes only in the X-API-Key header."""
    status, body, headers = http_get(f"{PLK_BASE}{path}", params,
                                     {"X-API-Key": plk_key()}, timeout=timeout)
    limits = {k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit")}
    if status != 200:
        raise RuntimeError(f"PLK {path} HTTP {status}: {body[:300]!r}")
    return json.loads(body), limits


def save_json(obj, path: Path, compress: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if compress:
        path = path.with_suffix(path.suffix + ".gz")
        path.write_bytes(gzip.compress(data))
    else:
        path.write_bytes(data)
    return path


def load_json(path: Path):
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data)


def find_station_id(name: str = STATION_NAME) -> int:
    """Return the PLK station ID for an exact station name. Cache it in data/."""
    cache = DATA / "plk_station.json"
    if cache.exists():
        c = load_json(cache)
        if c.get("name") == name:
            return int(c["id"])
    js, _ = plk_get("/dictionaries/stations", {"search": name.split()[0], "pageSize": 200})
    stations = js.get("stations", [])
    exact = [s for s in stations if s.get("name", "").strip().lower() == name.lower()]
    if not exact:
        names = ", ".join(s.get("name", "") for s in stations[:30])
        raise SystemExit(f"Station '{name}' not found. Candidates: {names}")
    save_json({"name": name, "id": exact[0]["id"], "candidates": stations}, cache)
    return int(exact[0]["id"])
