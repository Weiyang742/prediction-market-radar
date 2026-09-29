"""Kalshi v2 client.

Field mapping (verified against demo-api.kalshi.co sandbox responses, same
trade-api/v2 wire format as production):
  yes_bid_dollars  -> "0".."1" string -> yes_price
  yes_ask_dollars  -> spread = yes_ask - yes_bid (fraction)
  volume_24h_fp    -> volume_24h (string)
  liquidity_dollars-> depth (string)
  close_time       -> close_time
  settled binary   -> status in (settled, finalized), settlement_value_dollars -> outcome
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from data.config import Config
from data.http import BaseClient, as_float
from data.models import Market, Platform, PricePoint, utcnow

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"


def parse_kalshi_market(item: dict[str, Any], event: dict[str, Any],
                        asof_ts: datetime) -> Market:
    yes_bid = as_float(item.get("yes_bid_dollars") or 0.0)
    yes_ask = as_float(item.get("yes_ask_dollars") or yes_bid)
    close_time = None
    if item.get("close_time"):
        try:
            close_time = datetime.fromisoformat(str(item["close_time"]).replace("Z", "+00:00"))
        except ValueError:
            close_time = None
    return Market(
        market_id=str(item["ticker"]),
        platform=Platform.KALSHI,
        question=f"{event.get('title', '')}: {item.get('yes_sub_title', '')}".strip(": "),
        yes_price=yes_bid,
        volume_24h=as_float(item.get("volume_24h_fp") or item.get("volume_24h")
                            or item.get("volume_fp") or 0.0),
        spread=max(0.0, yes_ask - yes_bid),
        close_time=close_time,
        depth=as_float(item.get("liquidity_dollars") or item.get("liquidity") or 0.0),
        group_key=str(event.get("event_ticker") or ""),
        theme=None,
        series_ticker=event.get("series_ticker") or None,
        asof_ts=asof_ts,
        raw=item,
    )


def parse_kalshi_settled(item: dict[str, Any], event: dict[str, Any],
                         asof_ts: datetime) -> dict[str, Any]:
    outcome = as_float(item.get("settlement_value_dollars") or 0.0)
    resolved = item.get("settlement_ts") or item.get("settle_time") or item.get("close_time")
    # Binary settlement: ~1.0 = YES, ~0.0 = NO.  Anything else (e.g. 0.5) is a
    # void/refund, NOT a NO — do not map it to 0.0 or the GT is polluted.
    if outcome >= 0.99:
        result = 1.0
    elif outcome <= 0.01:
        result = 0.0
    else:
        result = None
    return {
        "market_id": str(item["ticker"]),
        "series_ticker": event.get("series_ticker", ""),
        "question": f"{event.get('title', '')}: {item.get('yes_sub_title', '')}".strip(": "),
        "outcome": result,
        "resolved_at": datetime.fromisoformat(str(resolved).replace("Z", "+00:00")) if resolved
        else asof_ts,
        "source": "kalshi_settled",
    }


def parse_kalshi_candle(item: dict[str, Any], ticker: str, asof_ts: datetime) -> PricePoint:
    bid = as_float((item.get("yes_bid") or {}).get("close_dollars") or 0.0)
    ask = as_float((item.get("yes_ask") or {}).get("close_dollars") or 0.0)
    return PricePoint(
        market_id=ticker,
        ts=datetime.fromtimestamp(as_float(item["end_period_ts"]), tz=timezone.utc),
        price=round((bid + ask) / 2.0, 4),
        volume=as_float(item.get("volume_fp") or 0.0),
        asof_ts=asof_ts,
    )


class KalshiClient(BaseClient):
    def __init__(self, config: Config) -> None:
        super().__init__(config, config.kalshi_rate_limit_rps)
        self.base_url = os.environ.get("KALSHI_BASE_URL", KALSHI_BASE).rstrip("/")

    def fetch_events(self, limit: int = 200, cursor: str | None = None,
                     status: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        return self.get_json(f"{self.base_url}/events", params)

    def fetch_markets(self, limit: int = 200) -> list[Market]:
        body = self.fetch_events(limit=limit)
        asof_ts = utcnow()
        return [parse_kalshi_market(item, event, asof_ts)
                for event in body.get("events", [])
                for item in event.get("markets", [])]

    def fetch_settled(self, limit: int = 200) -> list[dict[str, Any]]:
        body = self.fetch_events(limit=limit, status="settled")
        asof_ts = utcnow()
        out: list[dict[str, Any]] = []
        for event in body.get("events", []):
            for item in event.get("markets", []):
                # Kalshi reports settled binary markets as "finalized" in demo
                # and "settled" in production; accept both.
                if item.get("status") in ("settled", "finalized") \
                        and item.get("market_type") == "binary":
                    out.append(parse_kalshi_settled(item, event, asof_ts))
        return out

    def fetch_candles(self, series_ticker: str, ticker: str,
                      start_ts: int, end_ts: int, period_interval: int = 60) -> list[PricePoint]:
        body = self.get_json(f"{self.base_url}/series/{series_ticker}/markets/{ticker}/candlesticks",
                             {"start_ts": start_ts, "end_ts": end_ts,
                              "period_interval": period_interval})
        asof_ts = utcnow()
        return [parse_kalshi_candle(item, ticker, asof_ts)
                for item in body.get("candlesticks", [])]
