"""Public, read-only market data for the paper-trading workflow.

Sources:
  - BitgetPublicRestSource: GET-only requests to PUBLIC market-data endpoints
    under https://api.bitget.com/api/v2/mix/market/ (allowlisted). No auth
    headers are ever attached; any URL outside the allowlist is rejected
    before a request is made. Envelope-validated ({code:"00000"}); failures
    raise MarketDataError and are reported - never papered over with
    fabricated bars.
  - SyntheticSource: deterministic seeded random walk for offline tests and
    workflow demos. Always labeled "synthetic"; never presented as real data.
  - CacheSource: bars previously stored under output/paper/data/<source>/.

Endpoint note: the installed SDK documents the Playbook-runtime kline path
(/inner/v1/agent-data/...), which requires the managed runtime. For local
paper runs this module uses Bitget's public v2 mix-market candles family with
runtime validation of the response envelope and row shape. If the live shape
ever differs, the run fails loudly with a report instead of inventing data.
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

PUBLIC_HOST = "https://api.bitget.com"
ALLOWED_PATH_PREFIXES = ("/api/v2/mix/market/", "/api/v2/spot/market/")
USER_AGENT = "paper-trading/1.0 (read-only public market data; no auth)"

# Bitget v2 granularity tokens for the intervals the strategy uses. Validated
# at runtime against the live envelope; a mismatch raises, never fabricates.
GRANULARITY_MAP = {"4h": "4H", "1d": "1D"}
INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
# Minimum CLOSED bars required before decisions are trustworthy:
# daily EMA200 needs >=200 daily bars; the strategy fetches 120 x 4h.
MIN_BARS = {"1d": 210, "4h": 120}
MAX_PAGES = 8          # hard cap on pagination; never hammer the public API
REQUEST_TIMEOUT_S = 15


class MarketDataError(RuntimeError):
    """Public data could not be retrieved or validated. Never fabricate."""


class InsufficientDataError(MarketDataError):
    """Not enough (or too stale) history to make honest decisions."""


@dataclass(frozen=True)
class KlineBar:
    time_ms: int      # bar OPEN time (UTC ms), per Bitget kline semantics
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_row(self) -> dict:
        return {
            "time": self.time_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def _get_json(url: str, timeout: int = REQUEST_TIMEOUT_S):
    """GET a public URL. Allowlist-enforced; no auth headers; loud failures."""
    if not url.startswith(PUBLIC_HOST):
        raise MarketDataError(f"blocked: non-public host in URL {url!r}")
    path = url[len(PUBLIC_HOST):].split("?", 1)[0]
    if not path.startswith(ALLOWED_PATH_PREFIXES):
        raise MarketDataError(f"blocked: path {path!r} is not a public market-data endpoint")
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            if isinstance(payload, dict):
                detail = f": {payload.get('code')} {payload.get('msg')}".rstrip()
        except Exception:
            pass
        raise MarketDataError(f"HTTP {exc.code} for {path}{detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MarketDataError(f"request failed for {path}: {exc}") from exc
    if not isinstance(body, dict) or str(body.get("code")) != "00000":
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else "unexpected payload"
        raise MarketDataError(f"API code {code}: {msg}")
    return body.get("data")


def parse_candle_row(row) -> Optional[KlineBar]:
    """Parse one v2 mix candles row: [ts, open, high, low, close, baseVol, ...].

    Returns None for anything malformed - invalid data is dropped and counted,
    never coerced into a fake bar.
    """
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    try:
        ts = int(row[0])
        values = [float(row[index]) for index in range(1, 5)]
        volume = float(row[5])
    except (TypeError, ValueError):
        return None
    if ts <= 0 or not all(math.isfinite(value) for value in values) or not math.isfinite(volume):
        return None
    open_, high, low, close = values
    if high < low or high < max(open_, close) or low > min(open_, close):
        return None
    return KlineBar(ts, open_, high, low, close, volume)


def assert_fresh(bars: List[KlineBar], interval: str, now_ms: Optional[int] = None, tolerance_intervals: int = 2) -> None:
    """Refuse to act on a stalled feed (per SDK kline freshness guidance)."""
    if not bars:
        raise InsufficientDataError(f"no closed {interval} bars returned")
    step = INTERVAL_MS[interval]
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    age = now - (bars[-1].time_ms + step)
    if age > tolerance_intervals * step:
        raise InsufficientDataError(
            f"stale feed: newest closed {interval} bar is {age // step} intervals old (> {tolerance_intervals})"
        )


class BitgetPublicRestSource:
    """Read-only public REST klines for Bitget USDT-FUTURES contracts."""

    name = "bitget_public_rest"

    def __init__(self, product_type: str = "USDT-FUTURES", page_limit: int = 200) -> None:
        self.product_type = product_type
        self.page_limit = page_limit

    def fetch(self, symbol: str, interval: str, end_ms: Optional[int] = None, bars_needed: Optional[int] = None) -> List[KlineBar]:
        granularity = GRANULARITY_MAP.get(interval)
        if granularity is None:
            raise MarketDataError(f"unsupported interval {interval!r} (supported: {sorted(GRANULARITY_MAP)})")
        step = INTERVAL_MS[interval]
        target = bars_needed or MIN_BARS.get(interval, 200)
        collected: Dict[int, KlineBar] = {}
        cursor = end_ms or int(time.time() * 1000)
        malformed = 0
        for _page in range(MAX_PAGES):
            url = (
                f"{PUBLIC_HOST}/api/v2/mix/market/candles?symbol={symbol}"
                f"&productType={self.product_type}&granularity={granularity}"
                f"&limit={self.page_limit}&endTime={cursor}"
            )
            rows = _get_json(url)
            if not isinstance(rows, list):
                raise MarketDataError(f"unexpected candles payload for {symbol} {interval}")
            parsed = []
            for row in rows:
                bar = parse_candle_row(row)
                if bar is None:
                    malformed += 1
                else:
                    parsed.append(bar)
            for bar in parsed:
                collected[bar.time_ms] = bar
            if not parsed:
                break  # history exhausted (or endpoint shape changed -> reported elsewhere)
            oldest = min(bar.time_ms for bar in parsed)
            if len(collected) >= target:
                break
            cursor = oldest - 1
        bars = [collected[key] for key in sorted(collected)]
        now_ms = int(time.time() * 1000)
        bars = [bar for bar in bars if bar.time_ms + step <= now_ms]  # closed bars only
        if malformed:
            # Surface data-quality problems instead of hiding them.
            print(f"[paper:data] WARNING {symbol} {interval}: dropped {malformed} malformed row(s)")
        return bars

    def fetch_funding_rate(self, symbol: str) -> List[dict]:
        """Current funding rate only - public history is not documented, and we
        never fabricate it. Empty list = unavailable (gates fail closed)."""
        url = f"{PUBLIC_HOST}/api/v2/mix/market/current-fund-rate?symbol={symbol}&productType={self.product_type}"
        try:
            data = _get_json(url)
        except MarketDataError:
            return []
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        out = []
        for row in rows:
            try:
                out.append({"funding_rate": float(row.get("fundingRate"))})
            except (AttributeError, TypeError, ValueError):
                continue
        return out


class SyntheticSource:
    """Deterministic seeded random walk for offline tests and demos.

    Output is always labeled synthetic. It exists so the workflow, simulator
    and dashboard can be exercised end-to-end without network access - it is
    never presented as real market data.
    """

    name = "synthetic"

    def __init__(self, seed: int = 20260921) -> None:
        self.seed = seed

    def _base_price(self, symbol: str) -> float:
        return 50.0 + (zlib.crc32(symbol.encode("utf-8")) % 4000) / 10.0

    def fetch(self, symbol: str, interval: str, end_ms: Optional[int] = None, bars_needed: Optional[int] = None) -> List[KlineBar]:
        if interval not in INTERVAL_MS:
            raise MarketDataError(f"unsupported interval {interval!r}")
        step = INTERVAL_MS[interval]
        target = bars_needed or MIN_BARS.get(interval, 200)
        anchor = end_ms if end_ms is not None else int(time.time() * 1000)
        end = anchor // step * step
        if end + step > anchor:
            end -= step  # the last bar must be CLOSED relative to the anchor
        rng = random.Random(f"{self.seed}:{symbol}:{interval}")
        price = self._base_price(symbol)
        bars: List[KlineBar] = []
        for index in range(target):
            ts = end - step * (target - 1 - index)
            cycle = math.sin(index / 17.0) * 0.006
            drift = 0.0006
            shock = rng.uniform(-0.004, 0.004)
            open_ = price
            close = max(1.0, price * (1.0 + drift + cycle + shock))
            high = max(open_, close) * (1.0 + abs(rng.uniform(0.0, 0.002)))
            low = min(open_, close) * (1.0 - abs(rng.uniform(0.0, 0.002)))
            volume = rng.uniform(80.0, 140.0) * (2.2 if rng.random() < 0.12 else 1.0)
            bars.append(KlineBar(ts, round(open_, 6), round(high, 6), round(low, 6), round(close, 6), round(volume, 4)))
            price = close
        return bars

    def fetch_funding_rate(self, symbol: str) -> List[dict]:
        return [{"funding_rate": 0.00005}]


class CacheSource:
    """Replays bars previously stored by any source (offline reruns)."""

    name = "cache"

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def fetch(self, symbol: str, interval: str, end_ms: Optional[int] = None, bars_needed: Optional[int] = None) -> List[KlineBar]:
        path = self._path(symbol, interval)
        if not path.exists():
            raise InsufficientDataError(f"no cached bars for {symbol} {interval} at {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        bars = [KlineBar(**row) for row in payload.get("bars", [])]
        if end_ms is not None:
            bars = [bar for bar in bars if bar.time_ms <= end_ms]
        if bars_needed:
            bars = bars[-bars_needed:]
        return bars

    def fetch_funding_rate(self, symbol: str) -> List[dict]:
        return []  # funding history is never cached/fabricated; gate fails closed

    def _path(self, symbol: str, interval: str) -> Path:
        return self.cache_dir / f"{symbol}_{interval}.json"


def save_bars(cache_dir: Path, source_name: str, symbol: str, interval: str, bars: List[KlineBar]) -> Path:
    directory = Path(cache_dir) / source_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{symbol}_{interval}.json"
    payload = {
        "source": source_name,
        "symbol": symbol,
        "interval": interval,
        "saved_at_ms": int(time.time() * 1000),
        "bars": [bar.__dict__ for bar in bars],
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return path


def build_source(name: str, cache_dir: Path, seed: int = 20260921):
    if name == "rest":
        return BitgetPublicRestSource()
    if name == "synthetic":
        return SyntheticSource(seed=seed)
    if name == "cache":
        return CacheSource(cache_dir)
    raise MarketDataError(f"unknown source {name!r}")
