"""Polymarket (gamma / CLOB) client.

Field mapping (V3.1 §2.3 / §6.1):
  outcomePrices  -> JSON string array -> yes_price = float(outcomePrices[0])
  volume/volumeNum -> volume_24h
  bestBid/bestAsk  -> spread (fraction); list items have no spread field
  liquidity/liquidityNum -> depth (USD, liquidity proxy)
  endDate        -> close_time

Verification status: ``parse_polymarket_market`` is exercised against a real
V2 fixture borrowed from livetennisapi/polymarket-tennis
(tests/fixtures/polymarket_markets.json) — gamma-api.polymarket.com itself is
SNI-blocked in this build environment.  ``parse_polymarket_closed`` is written
against the documented schema but has no real fixture (no OSS project ships a
recorded closed-list response).  ``fetch_candles``'s endpoint is verified
against the official CLOB OpenAPI and Polymarket/py-sdk.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import httpx

from data.config import Config
from data.http import BaseClient, as_float
from data.models import Market, Platform, PricePoint, utcnow

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def parse_polymarket_market(item: dict[str, Any], asof_ts: datetime) -> Market:
    outcome_prices = json.loads(item.get("outcomePrices") or '["0", "0"]')
    yes_price = as_float(outcome_prices[0])
    close_time = None
    if item.get("endDate"):
        try:
            close_time = datetime.fromisoformat(str(item["endDate"]).replace("Z", "+00:00"))
        except ValueError:
            close_time = None
    # gamma V2 has volume/volumeNum and liquidity/liquidityNum; spread is not a
    # list-item field — derive it from bestBid/bestAsk when present (single-market
    # endpoint), else leave 0.
    best_bid = item.get("bestBid")
    best_ask = item.get("bestAsk")
    if best_bid is not None and best_ask is not None:
        spread = max(0.0, as_float(best_ask) - as_float(best_bid))
    else:
        spread = as_float(item.get("spread") or 0.0)
    return Market(
        market_id=str(item["id"]),
        platform=Platform.POLYMARKET,
        question=str(item.get("question") or ""),
        yes_price=yes_price,
        volume_24h=as_float(item.get("volume") or item.get("volumeNum") or 0.0),
        spread=spread,
        close_time=close_time,
        depth=as_float(item.get("liquidity") or item.get("liquidityNum") or 0.0),
        group_key=item.get("groupItemTitle"),
        theme=None,
        asof_ts=asof_ts,
        raw=item,
    )


def parse_polymarket_closed(item: dict[str, Any], asof_ts: datetime) -> dict[str, Any]:
    """Extract outcome (0/1) from a gamma closed market item.

    Uses the collapsed outcomePrices ["1","0"] per V3.1 §6.1.  The collapse
    behaviour is listed as 待抽样确认 and is UNVERIFIED here.
    """
    outcome_prices = json.loads(item.get("outcomePrices") or '["0", "0"]')
    resolved = item.get("closedTime") or item.get("endDate")
    return {
        "market_id": str(item["id"]),
        "outcome": 1.0 if as_float(outcome_prices[0]) > 0.5 else 0.0,
        "resolved_at": datetime.fromisoformat(str(resolved).replace("Z", "+00:00")) if resolved
        else asof_ts,
        "source": "polymarket_gamma_closed",
    }


class PolymarketClient(BaseClient):
    def __init__(self, config: Config) -> None:
        super().__init__(config, config.polymarket_rate_limit_rps)
        self.gamma_base = os.environ.get("POLYMARKET_GAMMA_URL", GAMMA_BASE).rstrip("/")
        self.clob_base = os.environ.get("POLYMARKET_CLOB_URL", CLOB_BASE).rstrip("/")

    def _after_response(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("Poly-RateLimit-Remaining")
        if remaining is not None:
            try:
                if int(remaining) <= 0:
                    time.sleep(0.5)
            except ValueError:
                pass

    def fetch_markets(self, limit: int = 500, offset: int = 0,
                      active: bool = True, closed: bool = False) -> list[Market]:
        body = self.get_json(f"{self.gamma_base}/markets", {
            "limit": limit, "offset": offset,
            "active": str(active).lower(), "closed": str(closed).lower(),
        })
        asof_ts = utcnow()
        return [parse_polymarket_market(m, asof_ts) for m in body] if isinstance(body, list) else []

    def fetch_closed_markets(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        body = self.get_json(f"{self.gamma_base}/markets", {
            "limit": limit, "offset": offset, "closed": "true",
        })
        asof_ts = utcnow()
        return [parse_polymarket_closed(m, asof_ts) for m in body] if isinstance(body, list) else []

    def resolve_clob_token_id(self, market_id: str) -> str | None:
        """Best-effort resolution of a clobTokenId for a gamma market id.

        UNVERIFIED against a real response (host SNI-blocked here; the borrowed
        fixture list has no ``clobTokenIds``): queries the gamma single-market
        endpoint and reads the first entry of its ``clobTokenIds`` JSON-string
        array.  Returns ``None`` when the field is absent/empty so callers
        degrade instead of guessing.
        """
        body = self.get_json(f"{self.gamma_base}/markets/{market_id}")
        if not isinstance(body, dict):
            return None
        raw = body.get("clobTokenIds")
        if not raw:
            return None
        try:
            tokens = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return None
        return str(tokens[0]) if tokens else None

    def fetch_candles(self, clob_token_id: str, interval: str = "1h") -> list[PricePoint]:
        """Historical price candles.

        Verified against the official CLOB OpenAPI and `Polymarket/py-sdk`
        (`clob.py` price-history action): `GET /prices-history?market=<assetId>&
        interval=<interval>`, where `market` is a `clobTokenIds` entry (not the
        conditionId).  `interval` allowlist in the SDK is max/1w/1d/6h/1h —
        `1m` exists in the spec but is not in the SDK enum, hence default 1h.
        Response: `{"history": [{"t": <unix s>, "p": <float>}, ...]}`.
        """
        body = self.get_json(f"{self.clob_base}/prices-history", {
            "market": clob_token_id, "interval": interval,
        })
        asof_ts = utcnow()
        return [PricePoint(
            market_id=clob_token_id,
            ts=datetime.fromtimestamp(as_float(row.get("t"))),
            price=as_float(row.get("p")),
            volume=0.0,
            asof_ts=asof_ts,
        ) for row in body.get("history", [])]
