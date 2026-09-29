"""阶段 0 probe: fetch the endpoints and save raw responses as fixtures.

Usage:
  python -m data.probe                       # production hosts
  KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2 python -m data.probe

Each endpoint is fetched independently; a blocked endpoint is reported as FAIL
and the others still save (no silent success).  Saves raw (unmodified) JSON
under tests/fixtures/.  All parsing code is developed against these fixtures.

Endpoints (V3.1 delivery contract §三 阶段 0):
  1. gamma  /markets                       -> polymarket_markets.json
  2. gamma  /markets?closed=true           -> polymarket_closed.json
  3. kalshi /events?with_nested_markets    -> kalshi_events.json
  4. kalshi /events?status=settled         -> kalshi_settled.json
  5. kalshi series candlesticks            -> kalshi_candles.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from data.kalshi_client import KALSHI_BASE
from data.polymarket_client import GAMMA_BASE

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _fetch(url: str, name: str, params: dict | None = None) -> bool:
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
        out = FIXTURES / name
        out.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")
        print(f"OK   {name}  <- {url}")
        return True
    except Exception as exc:  # report and continue; never fabricate a fixture
        print(f"FAIL {name}  ({url}): {exc}", file=sys.stderr)
        return False


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    kalshi = os.environ.get("KALSHI_BASE_URL", KALSHI_BASE)
    ok = 0
    ok += _fetch(f"{GAMMA_BASE}/markets", "polymarket_markets.json",
                 {"limit": 50, "active": "true"})
    ok += _fetch(f"{GAMMA_BASE}/markets", "polymarket_closed.json",
                 {"limit": 50, "closed": "true"})
    ok += _fetch(f"{kalshi}/events", "kalshi_events.json",
                 {"limit": 20, "with_nested_markets": "true"})
    ok += _fetch(f"{kalshi}/events", "kalshi_settled.json",
                 {"limit": 50, "status": "settled", "with_nested_markets": "true"})

    try:
        events = json.loads((FIXTURES / "kalshi_events.json").read_text())
        series = events["events"][0]["series_ticker"]
        ticker = events["events"][0]["markets"][0]["ticker"]
        now = int(time.time())
        ok += _fetch(f"{kalshi}/series/{series}/markets/{ticker}/candlesticks",
                     "kalshi_candles.json",
                     {"start_ts": now - 86400, "end_ts": now, "period_interval": 60})
    except Exception as exc:
        print(f"SKIP kalshi_candles.json: {exc}", file=sys.stderr)

    print(f"probe done: {ok}/5 endpoints succeeded")
    return 0 if ok >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
