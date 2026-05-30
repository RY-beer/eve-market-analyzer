"""Pydantic v2 models for EVE market data and resolved trading routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MarketOrder(BaseModel):
    """A single market order returned by ``/markets/{region}/orders``.

    ESI exposes ``order_id``; we accept either ``id`` or ``order_id`` so the
    raw API payload validates without renaming. ``min_volume`` only matters
    for buy orders (the buyer refuses parcels below it).
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(alias="order_id")
    type_id: int
    location_id: int
    system_id: int | None = None
    price: float
    volume_remain: int
    min_volume: int = 1
    is_buy_order: bool


class LocationSummary(BaseModel):
    """Resolved location for a market order, sourced from the SDE.

    ``station_id`` / ``station_name`` are ``None`` when the order sits in a
    player-owned structure (citadel) that the SDE can't resolve.
    """

    model_config = ConfigDict(frozen=True)

    location_id: int
    station_id: int | None
    station_name: str | None
    system_id: int | None
    system_name: str | None
    region_id: int | None
    region_name: str | None
    security_status: float | None


class TradingRoute(BaseModel):
    """A matched buy/sell pairing for a single item type across two locations.

    ``buyable_volume`` is what the source sell order can supply; ``sellable_volume``
    is the maximum the destination buy order will take. ``tradable_volume`` is
    the smaller of the two — the actual quantity that can move on this hop.

    ``jumps`` and ``min_security`` describe the stargate route between the
    source and destination systems; both are ``None`` if no path could be
    resolved (e.g. one endpoint is in unsupported space).
    """

    model_config = ConfigDict(frozen=True)

    type_id: int
    item_name: str

    buy_price: float
    sell_price: float
    unit_profit: float
    absolute_profit: float

    buyable_volume: int
    sellable_volume: int
    tradable_volume: int

    source_location: LocationSummary
    destination_location: LocationSummary

    jumps: int | None = None
    min_security: float | None = None
