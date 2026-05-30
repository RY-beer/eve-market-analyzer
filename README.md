# EVE Market Analyzer

A desktop tool that finds profitable hauling routes in EVE Online by comparing
buy and sell orders across regional market hubs.

## Stack

- **NiceGUI** — desktop/web UI
- **Pydantic v2** — typed market-data models
- **httpx** (async) — HTTP client for the public [EVE ESI](https://esi.evetech.net/) API

## How it works

For a chosen item type the analyzer:

1. Fetches all market orders in the **source** and **destination** regions from ESI (paginated).
2. Drops price anomalies more than 3 standard deviations from the median.
3. Pairs the cheapest **sell** order in the source region with the most expensive **buy** order in the destination region.
4. Reports per-unit and total profit for the matched volume.

## Running

```sh
uv sync
uv run python -m src.ui.main
```

The UI opens at <http://localhost:8080>.

## Project layout

```
src/
├── config.py                # ESI base URL, timeouts, cache path, common region IDs
├── domain/
│   ├── models.py            # MarketOrder, TradingRoute
│   └── calculator.py        # anomaly filter + route matcher (pure functions)
├── infrastructure/
│   └── esi_client.py        # async httpx wrapper around ESI markets endpoints
└── ui/
    └── main.py              # NiceGUI dashboard
```

## Common region IDs

| Hub      | Region        | ID         |
| -------- | ------------- | ---------- |
| Jita     | The Forge     | 10000002   |
| Amarr    | Domain        | 10000043   |
| Dodixie  | Sinq Laison   | 10000032   |
| Rens     | Heimatar      | 10000030   |
| Hek      | Metropolis    | 10000042   |
