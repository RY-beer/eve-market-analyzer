"""NiceGUI dashboard for EVE Market Analyzer.

Two-column layout: scan parameters on the left, results table on the right.
The form supports:
  * Source/destination region, optional drill-down to constellation/system/
    station via cascading dropdowns.
  * Multi-select item picker (searchable by name + type id). Leaving it empty
    triggers a "scan everything in the region" pass.
  * Profit threshold + result cap (a region-wide scan can yield thousands of
    routes; the cap keeps the table renderable).

The scan handler is ``async`` so the UI stays responsive while ESI requests
are in flight.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nicegui import ui

from src.domain.calculator import find_profitable_routes
from src.domain.models import TradingRoute
from src.infrastructure.esi_client import ESIClient
from src.infrastructure.static_data import StaticData, get_static_data

# How many entries to surface in the searchable item picker. Quasar's q-select
# virtualises the dropdown, but listing all ~19k market types still bloats the
# initial render; we cap the user-visible options unless they're actively
# searching.
ITEM_PICKER_LIMIT = 2000

# Hard cap on rows displayed for a single scan, used when the user doesn't set
# their own. Region-wide scans can otherwise produce thousands of routes.
DEFAULT_RESULT_CAP = 200

TABLE_COLUMNS: list[dict[str, Any]] = [
    {"name": "type_id", "label": "Type ID", "field": "type_id", "align": "right", "sortable": True},
    {"name": "item_name", "label": "Item", "field": "item_name", "align": "left", "sortable": True},
    {"name": "source", "label": "Buy at", "field": "source", "align": "left"},
    {"name": "destination", "label": "Sell at", "field": "destination", "align": "left"},
    {"name": "buy_price", "label": "Buy", "field": "buy_price", "align": "right"},
    {"name": "sell_price", "label": "Sell", "field": "sell_price", "align": "right"},
    {"name": "buyable", "label": "Avail.", "field": "buyable", "align": "right"},
    {"name": "sellable", "label": "Max sell", "field": "sellable", "align": "right"},
    {"name": "tradable", "label": "Trade qty", "field": "tradable", "align": "right"},
    {"name": "jumps", "label": "Jumps", "field": "jumps", "align": "right", "sortable": True},
    {"name": "min_sec", "label": "Min sec", "field": "min_sec", "align": "right", "sortable": True},
    {"name": "unit_profit", "label": "Unit profit", "field": "unit_profit", "align": "right", "sortable": True},
    {"name": "absolute_profit", "label": "Total profit", "field": "absolute_profit", "align": "right", "sortable": True},
]

ANY_KEY = 0  # Sentinel select value meaning "no narrowing".


def _region_options(static: StaticData) -> dict[int, str]:
    return {r.region_id: f"{r.name} ({r.region_id})" for r in static.region_list()}


def _system_options(static: StaticData, region_id: int | None) -> dict[int, str]:
    opts: dict[int, str] = {ANY_KEY: "Any system in region"}
    if region_id is None:
        return opts
    for system in static.systems_in_region(region_id):
        opts[system.system_id] = f"{system.name}  (sec {system.security_status:.2f})"
    return opts


def _station_options(static: StaticData, system_id: int | None) -> dict[int, str]:
    opts: dict[int, str] = {ANY_KEY: "Any station in system"}
    if not system_id:
        return opts
    for station in static.stations_in_system(system_id):
        opts[station.station_id] = station.name
    return opts


def _item_options(static: StaticData, limit: int = ITEM_PICKER_LIMIT) -> dict[int, str]:
    """Build the (type_id -> label) map shown in the multi-select.

    Cap to ``limit`` entries so the initial render stays fast; the user can
    type in the search box to find any other item by name.
    """
    items = static.market_type_list()[:limit]
    return {t.type_id: f"{t.name} ({t.type_id})" for t in items}


def _format_location(loc) -> str:
    """Compact one-line location label for the results table."""
    if loc.station_name:
        return loc.station_name
    if loc.system_name:
        return f"{loc.system_name} (structure {loc.location_id})"
    return f"Unknown location {loc.location_id}"


def _format_route_row(route: TradingRoute) -> dict[str, Any]:
    return {
        "type_id": route.type_id,
        "item_name": route.item_name,
        "source": _format_location(route.source_location),
        "destination": _format_location(route.destination_location),
        "buy_price": f"{route.buy_price:,.2f}",
        "sell_price": f"{route.sell_price:,.2f}",
        "buyable": f"{route.buyable_volume:,}",
        "sellable": f"{route.sellable_volume:,}",
        "tradable": f"{route.tradable_volume:,}",
        "jumps": "—" if route.jumps is None else route.jumps,
        "min_sec": "—" if route.min_security is None else f"{route.min_security:.2f}",
        "unit_profit": f"{route.unit_profit:,.2f}",
        "absolute_profit": f"{route.absolute_profit:,.2f}",
    }


@ui.page("/")
def index() -> None:
    static = get_static_data()
    ui.dark_mode().enable()
    ui.colors(primary="#f59e0b")

    with ui.header().classes("items-center bg-zinc-900 text-amber-400 shadow-md"):
        ui.label("EVE Market Analyzer").classes("text-xl font-bold")
        ui.space()
        ui.label("Regional hauling arbitrage").classes("text-sm opacity-70")

    region_opts = _region_options(static)
    # The Forge (Jita) and Domain (Amarr) are the most common arbitrage pair;
    # default to them so the form is usable without scrolling.
    default_source = 10000002 if 10000002 in region_opts else next(iter(region_opts))
    default_dest = 10000043 if 10000043 in region_opts else next(iter(region_opts))

    with ui.row().classes("w-full no-wrap items-start"):
        with ui.card().classes("w-96 m-4 p-4 gap-3"):
            ui.label("Scan parameters").classes("text-lg font-semibold")

            ui.label("Source (where you buy)").classes("text-sm opacity-70 mt-2")
            source_region = ui.select(
                region_opts,
                label="Region",
                value=default_source,
                with_input=True,
            ).classes("w-full")
            source_system = ui.select(
                _system_options(static, default_source),
                label="System",
                value=ANY_KEY,
                with_input=True,
            ).classes("w-full")
            source_station = ui.select(
                _station_options(static, None),
                label="Station",
                value=ANY_KEY,
                with_input=True,
            ).classes("w-full")

            ui.label("Destination (where you sell)").classes("text-sm opacity-70 mt-2")
            destination_region = ui.select(
                region_opts,
                label="Region",
                value=default_dest,
                with_input=True,
            ).classes("w-full")
            destination_system = ui.select(
                _system_options(static, default_dest),
                label="System",
                value=ANY_KEY,
                with_input=True,
            ).classes("w-full")
            destination_station = ui.select(
                _station_options(static, None),
                label="Station",
                value=ANY_KEY,
                with_input=True,
            ).classes("w-full")

            ui.label("Items (leave empty to scan all)").classes("text-sm opacity-70 mt-2")
            item_select = ui.select(
                _item_options(static),
                label="Items",
                value=[],
                multiple=True,
                with_input=True,
            ).props("use-chips clearable").classes("w-full")
            ui.label(
                f"Showing first {ITEM_PICKER_LIMIT:,} items alphabetically; "
                "type to search any of the ~19k tradable types."
            ).classes("text-xs opacity-60")

            min_profit_input = ui.number(
                label="Minimum unit profit (ISK)",
                value=0.0,
                format="%.2f",
            ).classes("w-full")

            max_results_input = ui.number(
                label="Max routes to show",
                value=DEFAULT_RESULT_CAP,
                min=1,
                format="%d",
            ).classes("w-full")

            scan_button = ui.button("Scan Market").props("color=amber")
            status_label = ui.label("Idle.").classes("text-xs opacity-70")

        with ui.card().classes("flex-1 m-4 p-4 gap-2"):
            ui.label("Profitable routes").classes("text-lg font-semibold")
            table = ui.table(
                columns=TABLE_COLUMNS,
                rows=[],
                row_key="type_id",
                pagination=20,
            ).classes("w-full")

    # --- Cascading dropdowns -------------------------------------------------

    def refresh_source_systems() -> None:
        source_system.options = _system_options(static, source_region.value)
        source_system.value = ANY_KEY
        source_system.update()
        source_station.options = _station_options(static, None)
        source_station.value = ANY_KEY
        source_station.update()

    def refresh_source_stations() -> None:
        sys_id = source_system.value if source_system.value != ANY_KEY else None
        source_station.options = _station_options(static, sys_id)
        source_station.value = ANY_KEY
        source_station.update()

    def refresh_destination_systems() -> None:
        destination_system.options = _system_options(static, destination_region.value)
        destination_system.value = ANY_KEY
        destination_system.update()
        destination_station.options = _station_options(static, None)
        destination_station.value = ANY_KEY
        destination_station.update()

    def refresh_destination_stations() -> None:
        sys_id = destination_system.value if destination_system.value != ANY_KEY else None
        destination_station.options = _station_options(static, sys_id)
        destination_station.value = ANY_KEY
        destination_station.update()

    source_region.on_value_change(lambda _e: refresh_source_systems())
    source_system.on_value_change(lambda _e: refresh_source_stations())
    destination_region.on_value_change(lambda _e: refresh_destination_systems())
    destination_system.on_value_change(lambda _e: refresh_destination_stations())

    # --- Scan handler --------------------------------------------------------

    async def on_scan() -> None:
        scan_button.disable()
        try:
            src_region = int(source_region.value)
            dst_region = int(destination_region.value)
            src_system = source_system.value if source_system.value != ANY_KEY else None
            dst_system = destination_system.value if destination_system.value != ANY_KEY else None
            src_station = source_station.value if source_station.value != ANY_KEY else None
            dst_station = destination_station.value if destination_station.value != ANY_KEY else None

            type_ids: list[int] = list(item_select.value or [])
            min_profit = float(min_profit_input.value or 0.0)
            max_results = int(max_results_input.value or DEFAULT_RESULT_CAP)

            if src_region == dst_region and src_station == dst_station and src_system == dst_system:
                ui.notify(
                    "Source and destination must differ for a hauling route.",
                    type="warning",
                )
                return

            status_label.text = (
                f"Fetching {'all orders' if not type_ids else f'{len(type_ids)} item(s)'} "
                f"from both regions..."
            )

            async with ESIClient() as client:
                if type_ids and len(type_ids) <= 5:
                    # Few items: fetch per-type calls (smaller payloads).
                    src_lists, dst_lists = await asyncio.gather(
                        asyncio.gather(
                            *(client.fetch_orders(src_region, type_id=t) for t in type_ids)
                        ),
                        asyncio.gather(
                            *(client.fetch_orders(dst_region, type_id=t) for t in type_ids)
                        ),
                    )
                    source_orders = [o for lst in src_lists for o in lst]
                    dest_orders = [o for lst in dst_lists for o in lst]
                else:
                    # Many items or scan-all: one bulk paginated fetch per region.
                    source_orders, dest_orders = await asyncio.gather(
                        client.fetch_orders(src_region, type_id=None),
                        client.fetch_orders(dst_region, type_id=None),
                    )

            status_label.text = (
                f"Fetched {len(source_orders):,} source / {len(dest_orders):,} dest orders. "
                "Matching routes..."
            )

            routes = find_profitable_routes(
                source_orders=source_orders,
                destination_orders=dest_orders,
                static=static,
                type_ids=type_ids or None,
                source_station_id=src_station,
                source_system_id=src_system,
                destination_station_id=dst_station,
                destination_system_id=dst_system,
                minimum_profit=min_profit,
                max_results=max_results,
            )

            table.rows = [_format_route_row(r) for r in routes]
            table.update()
            status_label.text = (
                f"Showing {len(routes)} route(s) — top by total profit."
                if routes
                else "No profitable route at the current threshold."
            )
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            status_label.text = f"Error: {exc}"
            ui.notify(f"Scan failed: {exc}", type="negative")
        finally:
            scan_button.enable()

    scan_button.on_click(on_scan)


def main() -> None:
    # Warm the static-data cache before serving so the first /-page hit doesn't
    # eat the 0.5-1s load.
    get_static_data()
    ui.run(title="EVE Market Analyzer", reload=False, dark=True)


if __name__ in {"__main__", "__mp_main__"}:
    main()
