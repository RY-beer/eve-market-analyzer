"""Loaders and indexes over CCP's bundled EVE static data (jsonl).

Two responsibilities:
  * Build name/parent-key lookups (type, region, constellation, system, station)
    so the UI can show readable labels and cascade region -> system -> station.
  * Build the stargate graph and expose ``compute_route`` for jump count and
    minimum security status across a path.

A single module-level :class:`StaticData` instance is constructed lazily via
:func:`get_static_data`; loading touches ~150 MB of jsonl, so we only do it
once per process.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from src.config import Config
from src.config import config as default_config


@dataclass(frozen=True)
class MarketType:
    type_id: int
    name: str
    market_group_id: int


@dataclass(frozen=True)
class Region:
    region_id: int
    name: str


@dataclass(frozen=True)
class Constellation:
    constellation_id: int
    name: str
    region_id: int


@dataclass(frozen=True)
class SolarSystem:
    system_id: int
    name: str
    constellation_id: int
    region_id: int
    security_status: float


@dataclass(frozen=True)
class Station:
    station_id: int
    name: str
    system_id: int
    region_id: int


@dataclass(frozen=True)
class RouteResult:
    """Result of a stargate-graph search between two systems.

    ``jumps`` is the number of stargate hops (0 if same system, ``None`` if no
    high/low/null-sec path exists — e.g. one endpoint is in wormhole space).
    ``min_security`` is the lowest ``securityStatus`` among the systems on the
    path (including endpoints).
    """

    jumps: int | None
    min_security: float | None
    path: tuple[int, ...]


@dataclass
class StaticData:
    """In-memory indexes over the CCP SDE export.

    Fields are populated by :func:`_load_all`. Outside callers should access
    them through the helper methods rather than reaching into the dicts.
    """

    market_types: dict[int, MarketType] = field(default_factory=dict)
    regions: dict[int, Region] = field(default_factory=dict)
    constellations: dict[int, Constellation] = field(default_factory=dict)
    systems: dict[int, SolarSystem] = field(default_factory=dict)
    stations: dict[int, Station] = field(default_factory=dict)
    # system_id -> list of neighbouring system_ids (via stargates)
    stargate_graph: dict[int, list[int]] = field(default_factory=dict)
    # region_id -> ordered list of systems in that region (UI cascade)
    systems_by_region: dict[int, list[SolarSystem]] = field(default_factory=dict)
    # system_id -> ordered list of NPC stations in that system (UI cascade)
    stations_by_system: dict[int, list[Station]] = field(default_factory=dict)

    # --- Lookups --------------------------------------------------------

    def market_type_list(self) -> list[MarketType]:
        return sorted(self.market_types.values(), key=lambda t: t.name)

    def region_list(self) -> list[Region]:
        return sorted(self.regions.values(), key=lambda r: r.name)

    def systems_in_region(self, region_id: int) -> list[SolarSystem]:
        return self.systems_by_region.get(region_id, [])

    def stations_in_system(self, system_id: int) -> list[Station]:
        return self.stations_by_system.get(system_id, [])

    def resolve_location(
        self, location_id: int
    ) -> tuple[Station | None, SolarSystem | None, Region | None]:
        """Resolve an ESI ``location_id`` to its station/system/region.

        Player-built citadels (location_id >= 1e12) are not in the SDE and
        return ``(None, None, None)`` — callers should treat them as unknown.
        """
        station = self.stations.get(location_id)
        if station is None:
            return (None, None, None)
        system = self.systems.get(station.system_id)
        region = self.regions.get(station.region_id) if system else None
        return (station, system, region)

    # --- Route finding --------------------------------------------------

    def compute_route(self, from_system: int, to_system: int) -> RouteResult:
        """BFS over the stargate graph; returns jump count + min security."""
        if from_system == to_system:
            sec = self.systems[from_system].security_status if from_system in self.systems else None
            return RouteResult(jumps=0, min_security=sec, path=(from_system,))

        if from_system not in self.stargate_graph or to_system not in self.systems:
            return RouteResult(jumps=None, min_security=None, path=())

        # BFS with parent tracking so we can reconstruct the path for min-sec.
        parent: dict[int, int] = {from_system: from_system}
        queue: deque[int] = deque([from_system])
        while queue:
            node = queue.popleft()
            if node == to_system:
                break
            for neighbour in self.stargate_graph.get(node, ()):
                if neighbour not in parent:
                    parent[neighbour] = node
                    queue.append(neighbour)
        else:
            # Queue drained without finding to_system.
            if to_system not in parent:
                return RouteResult(jumps=None, min_security=None, path=())

        # Reconstruct path.
        path: list[int] = []
        cursor = to_system
        while cursor != from_system:
            path.append(cursor)
            cursor = parent[cursor]
        path.append(from_system)
        path.reverse()

        min_sec = min(
            self.systems[s].security_status for s in path if s in self.systems
        )
        return RouteResult(jumps=len(path) - 1, min_security=min_sec, path=tuple(path))


# --- Loader ------------------------------------------------------------------

_EN = "en"


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _english_name(name_field: object, fallback: str = "") -> str:
    """Names in the SDE are localised dicts; pick the English form."""
    if isinstance(name_field, dict):
        en = name_field.get(_EN)
        if isinstance(en, str) and en:
            return en
    if isinstance(name_field, str):
        return name_field
    return fallback


def _load_all(static_dir: Path) -> StaticData:
    data = StaticData()

    # Regions ---------------------------------------------------------------
    for row in _iter_jsonl(static_dir / "mapRegions.jsonl"):
        region_id = int(row["_key"])
        name = _english_name(row.get("name"), fallback=f"Region {region_id}")
        data.regions[region_id] = Region(region_id=region_id, name=name)

    # Constellations --------------------------------------------------------
    # Some constellation files use `regionID`; trust that field directly.
    for row in _iter_jsonl(static_dir / "mapConstellations.jsonl"):
        const_id = int(row["_key"])
        region_id = int(row["regionID"])
        name = _english_name(row.get("name"), fallback=f"Constellation {const_id}")
        data.constellations[const_id] = Constellation(
            constellation_id=const_id, name=name, region_id=region_id
        )

    # Solar systems ---------------------------------------------------------
    for row in _iter_jsonl(static_dir / "mapSolarSystems.jsonl"):
        system_id = int(row["_key"])
        const_id = int(row["constellationID"])
        region_id = data.constellations[const_id].region_id if const_id in data.constellations else int(row.get("regionID", 0))
        name = _english_name(row.get("name"), fallback=f"System {system_id}")
        sec = float(row.get("securityStatus", 0.0))
        system = SolarSystem(
            system_id=system_id,
            name=name,
            constellation_id=const_id,
            region_id=region_id,
            security_status=sec,
        )
        data.systems[system_id] = system
        data.systems_by_region.setdefault(region_id, []).append(system)

    for systems in data.systems_by_region.values():
        systems.sort(key=lambda s: s.name)

    # Stargates -> system adjacency ----------------------------------------
    # mapStargates rows include solarSystemID + destination.solarSystemID, which
    # is exactly the edge we need; the stargate IDs themselves aren't needed.
    for row in _iter_jsonl(static_dir / "mapStargates.jsonl"):
        src = int(row["solarSystemID"])
        dst = int(row["destination"]["solarSystemID"])
        data.stargate_graph.setdefault(src, []).append(dst)

    # Auxiliary tables for station naming -----------------------------------
    # NPC stations don't carry their in-game name; we synthesise one from the
    # operation type ("Plantation", "Trading Hub", ...) and the owning corp.
    operation_names: dict[int, str] = {}
    for row in _iter_jsonl(static_dir / "stationOperations.jsonl"):
        operation_names[int(row["_key"])] = _english_name(row.get("operationName"))

    corp_names: dict[int, str] = {}
    corps_path = static_dir / "npcCorporations.jsonl"
    if corps_path.exists():
        for row in _iter_jsonl(corps_path):
            corp_names[int(row["_key"])] = _english_name(row.get("name"))

    # NPC stations ----------------------------------------------------------
    for row in _iter_jsonl(static_dir / "npcStations.jsonl"):
        station_id = int(row["_key"])
        system_id = int(row["solarSystemID"])
        system = data.systems.get(system_id)
        region_id = system.region_id if system else 0
        op_name = operation_names.get(int(row.get("operationID", 0)), "Station")
        corp_name = corp_names.get(int(row.get("ownerID", 0)), "")
        system_label = system.name if system else f"System {system_id}"
        station_name = (
            f"{system_label} - {corp_name} {op_name}".strip()
            if corp_name
            else f"{system_label} - {op_name}"
        )
        station = Station(
            station_id=station_id,
            name=station_name,
            system_id=system_id,
            region_id=region_id,
        )
        data.stations[station_id] = station
        data.stations_by_system.setdefault(system_id, []).append(station)

    for stations in data.stations_by_system.values():
        stations.sort(key=lambda s: s.name)

    # Market types ----------------------------------------------------------
    # types.jsonl is ~140 MB; stream it and keep only entries that are actually
    # tradable (published + marketGroupID set).
    for row in _iter_jsonl(static_dir / "types.jsonl"):
        if not row.get("published"):
            continue
        market_group_id = row.get("marketGroupID")
        if market_group_id is None:
            continue
        type_id = int(row["_key"])
        name = _english_name(row.get("name"), fallback=f"Type {type_id}")
        data.market_types[type_id] = MarketType(
            type_id=type_id, name=name, market_group_id=int(market_group_id)
        )

    return data


# --- Singleton access --------------------------------------------------------

_singleton: StaticData | None = None
_singleton_lock = Lock()


def get_static_data(cfg: Config | None = None) -> StaticData:
    """Return the process-wide :class:`StaticData`, loading on first use."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            cfg = cfg or default_config
            _singleton = _load_all(cfg.static_data_dir)
    return _singleton
