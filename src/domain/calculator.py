"""Pure computation: outlier filtering and route matching.

Kept free of I/O so it stays trivially unit-testable. Takes already-fetched
orders plus a resolved :class:`~src.infrastructure.static_data.StaticData`
snapshot, and emits :class:`TradingRoute` rows annotated with locations,
quantities, jumps, and minimum security along the path.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable

from src.domain.models import LocationSummary, MarketOrder, TradingRoute
from src.infrastructure.static_data import StaticData

ANOMALY_STD_CUTOFF: float = 3.0


def filter_anomalies(orders: list[MarketOrder]) -> list[MarketOrder]:
    """Drop orders more than ``ANOMALY_STD_CUTOFF`` stdevs from the median price.

    Median + stdev (rather than mean) because EVE markets routinely contain
    extreme scam orders that drag a mean toward themselves and let the actual
    outliers survive.
    """
    if len(orders) < 2:
        return list(orders)

    prices = [o.price for o in orders]
    median = statistics.median(prices)
    stdev = statistics.pstdev(prices)
    if stdev == 0:
        return list(orders)

    threshold = ANOMALY_STD_CUTOFF * stdev
    return [o for o in orders if abs(o.price - median) <= threshold]


def _match_location(
    order: MarketOrder,
    *,
    station_id: int | None,
    system_id: int | None,
    static: StaticData,
) -> bool:
    """Does ``order`` sit inside the user-selected station/system narrowing?"""
    if station_id is not None:
        return order.location_id == station_id
    if system_id is not None:
        station = static.stations.get(order.location_id)
        return station is not None and station.system_id == system_id
    return True


def _location_summary(order: MarketOrder, static: StaticData) -> LocationSummary:
    station, system, region = static.resolve_location(order.location_id)
    return LocationSummary(
        location_id=order.location_id,
        station_id=station.station_id if station else None,
        station_name=station.name if station else None,
        system_id=system.system_id if system else None,
        system_name=system.name if system else None,
        region_id=region.region_id if region else None,
        region_name=region.name if region else None,
        security_status=system.security_status if system else None,
    )


def find_profitable_routes(
    source_orders: Iterable[MarketOrder],
    destination_orders: Iterable[MarketOrder],
    *,
    static: StaticData,
    type_ids: list[int] | None = None,
    source_station_id: int | None = None,
    source_system_id: int | None = None,
    destination_station_id: int | None = None,
    destination_system_id: int | None = None,
    minimum_profit: float = 0.0,
    max_results: int | None = None,
) -> list[TradingRoute]:
    """Build profitable routes between source and destination order pools.

    ``type_ids=None`` means "consider every market type present in the input
    orders" — the scan-all mode. ``source_station_id`` (or ``_system_id``)
    narrows where you'd be buying; the destination flags do the same for
    where you'd sell. When narrowing is omitted on a side, any location in
    that region is fair game.

    Returned rows are sorted by absolute profit descending; ``max_results``
    truncates the list so a region-wide scan doesn't drown the UI.
    """
    # Bucket orders by type so we only have to scan the input lists once.
    src_by_type: dict[int, list[MarketOrder]] = defaultdict(list)
    for o in source_orders:
        if o.is_buy_order:
            continue
        if not _match_location(
            o, station_id=source_station_id, system_id=source_system_id, static=static
        ):
            continue
        src_by_type[o.type_id].append(o)

    dst_by_type: dict[int, list[MarketOrder]] = defaultdict(list)
    for o in destination_orders:
        if not o.is_buy_order:
            continue
        if not _match_location(
            o,
            station_id=destination_station_id,
            system_id=destination_system_id,
            static=static,
        ):
            continue
        dst_by_type[o.type_id].append(o)

    candidate_types: Iterable[int]
    if type_ids:
        candidate_types = type_ids
    else:
        candidate_types = set(src_by_type) & set(dst_by_type)

    routes: list[TradingRoute] = []
    for type_id in candidate_types:
        sells = filter_anomalies(src_by_type.get(type_id, []))
        buys = filter_anomalies(dst_by_type.get(type_id, []))
        if not sells or not buys:
            continue

        best_buy = min(sells, key=lambda o: o.price)  # cheapest place to buy
        best_sell = max(buys, key=lambda o: o.price)  # priciest place to sell

        unit_profit = best_sell.price - best_buy.price
        if unit_profit < minimum_profit:
            continue

        buyable = best_buy.volume_remain
        sellable = best_sell.volume_remain
        tradable = min(buyable, sellable)
        if tradable <= 0:
            continue

        src_loc = _location_summary(best_buy, static)
        dst_loc = _location_summary(best_sell, static)

        jumps: int | None = None
        min_sec: float | None = None
        if src_loc.system_id is not None and dst_loc.system_id is not None:
            route = static.compute_route(src_loc.system_id, dst_loc.system_id)
            jumps = route.jumps
            min_sec = route.min_security

        item_name = static.market_types[type_id].name if type_id in static.market_types else f"Type {type_id}"

        routes.append(
            TradingRoute(
                type_id=type_id,
                item_name=item_name,
                buy_price=best_buy.price,
                sell_price=best_sell.price,
                unit_profit=unit_profit,
                absolute_profit=unit_profit * tradable,
                buyable_volume=buyable,
                sellable_volume=sellable,
                tradable_volume=tradable,
                source_location=src_loc,
                destination_location=dst_loc,
                jumps=jumps,
                min_security=min_sec,
            )
        )

    routes.sort(key=lambda r: r.absolute_profit, reverse=True)
    if max_results is not None:
        routes = routes[:max_results]
    return routes
