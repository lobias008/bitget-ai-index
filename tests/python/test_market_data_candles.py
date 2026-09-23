"""Regression tests for the Bitget v2 MIX candles request contract.

The mix-market candles endpoint (/api/v2/mix/market/candles) requires
uppercase hour/day granularity tokens (1H/4H/1D). The spot-style lowercase
tokens (1d/4h) make Bitget reject the call with HTTP 400, which previously
surfaced only as "HTTP 400 for /api/v2/mix/market/candles" because the error
body was discarded. These tests pin the uppercase wire granularity and that
the Bitget error body (code/msg) is surfaced. Offline: urlopen is
monkeypatched; the network is never touched and no order endpoint is called.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paper import market_data as md  # noqa: E402
from paper.market_data import BitgetPublicRestSource, MarketDataError  # noqa: E402

T0 = 1_758_067_200_000  # 2025-09-17T00:00:00Z


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestMixCandlesRequestContract(unittest.TestCase):
    def _capture_url(self, interval):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            return _FakeResponse({"code": "00000",
                                  "data": [[str(T0), "1", "1", "1", "1", "1"]]})

        original = md.urllib.request.urlopen
        md.urllib.request.urlopen = fake_urlopen
        try:
            BitgetPublicRestSource().fetch("BTCUSDT", interval, end_ms=T0, bars_needed=1)
        finally:
            md.urllib.request.urlopen = original
        return captured["url"]

    def test_daily_granularity_is_uppercase_for_mix_endpoint(self):
        url = self._capture_url("1d")
        self.assertIn("/api/v2/mix/market/candles", url)
        self.assertIn("granularity=1D", url)
        self.assertNotIn("granularity=1d", url)

    def test_four_hour_granularity_is_uppercase_for_mix_endpoint(self):
        url = self._capture_url("4h")
        self.assertIn("granularity=4H", url)
        self.assertNotIn("granularity=4h", url)

    def test_request_params_match_mix_contract(self):
        url = self._capture_url("1d")
        self.assertIn("symbol=BTCUSDT", url)
        self.assertIn("productType=USDT-FUTURES", url)
        self.assertIn("endTime=", url)
        self.assertNotIn("startTime=", url)


class TestHttpErrorBodyIsSurfaced(unittest.TestCase):
    def test_400_body_code_and_msg_are_included(self):
        body = json.dumps({"code": "40708", "msg": "granularity is not valid"}).encode("utf-8")
        err = urllib.error.HTTPError(
            "https://api.bitget.com/api/v2/mix/market/candles",
            400, "Bad Request", {}, io.BytesIO(body))

        def fake_urlopen(request, timeout=None):
            raise err

        original = md.urllib.request.urlopen
        md.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(MarketDataError) as ctx:
                md._get_json("https://api.bitget.com/api/v2/mix/market/candles?symbol=BTCUSDT")
        finally:
            md.urllib.request.urlopen = original
        message = str(ctx.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("40708", message)
        self.assertIn("granularity is not valid", message)


if __name__ == "__main__":
    unittest.main()