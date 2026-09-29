# Fixtures

The `*.json` files in this directory are the **raw, unmodified** responses from
the 阶段 0 probe endpoints (V3.1 delivery contract §三 阶段 0).

Generate them by running, from the repo root:

```bash
python -m data.probe                                        # production hosts
KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2 \
  python -m data.probe                                      # Kalshi sandbox host
```

Current state (build environment: mainland-China network):

| fixture | source | status |
|---|---|---|
| `polymarket_markets.json` | borrowed from `livetennisapi/polymarket-tennis` `tests/fixtures/gamma_events_tennis.json` (real recorded V2 gamma response, events' nested markets extracted) | present |
| `polymarket_closed.json` | `gamma-api.polymarket.com?closed=true` | **absent** — no OSS repo ships a real recorded closed-list fixture |
| `kalshi_events.json` | `demo-api.kalshi.co/trade-api/v2` | present (sandbox wire format) |
| `kalshi_settled.json` | `demo-api.kalshi.co/trade-api/v2` | present (sandbox) |
| `kalshi_candles.json` | `demo-api.kalshi.co/trade-api/v2` | present (sandbox) |

The Kalshi fixtures are **sandbox data** (`*.kalshi.co`, not production
`*.kalshi.com`) but use the same `trade-api/v2` wire format, so they validate
the parsers against real API responses.  They must not be used as live market
data.

`polymarket_markets.json` is **borrowed real V2 data** (credit:
[livetennisapi/polymarket-tennis](https://github.com/livetennisapi/polymarket-tennis),
MIT — see that repo's license).  It was recorded from a live Polymarket gamma
response and is used here to validate `parse_polymarket_market` against the real
V2 field shape (`outcomePrices` as a JSON string, `volumeNum`/`liquidityNum`).

The fixture-gated tests in `tests/test_clients.py` therefore pass for the three
Kalshi fixtures plus `polymarket_markets.json`, and **fail only for the
`polymarket_closed.json` test** (no real closed-list fixture exists to borrow;
do not fabricate one).
