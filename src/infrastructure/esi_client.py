"""Async EVE ESI client.

Thin wrapper around ``httpx.AsyncClient`` that:
  * pages through regional market-order results (parallelised after page 1,
    since ESI tells us the page count in the ``X-Pages`` header);
  * supports both single-type fetches and full-region scans (no ``type_id``).
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

import httpx

from src.config import Config
from src.config import config as default_config
from src.domain.models import MarketOrder

MAX_PARALLEL_PAGES = 8


class ESIClient:
    """Async client for the public EVE ESI markets API.

    Usage::

        async with ESIClient() as client:
            orders = await client.fetch_orders(region_id=10000002, type_id=34)
    """

    def __init__(self, cfg: Config | None = None) -> None:
        self._cfg = cfg or default_config
        self._client = httpx.AsyncClient(
            base_url=self._cfg.esi_base_url,
            timeout=self._cfg.request_timeout_seconds,
            headers={
                "User-Agent": self._cfg.esi_user_agent,
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_orders(
        self,
        region_id: int,
        type_id: int | None = None,
    ) -> list[MarketOrder]:
        """Return all market orders for ``region_id``.

        When ``type_id`` is ``None`` ESI returns every order in the region,
        which is the right shape for "scan everything" mode. After reading
        page 1 we discover ``X-Pages`` and fetch the remainder concurrently
        (bounded by :data:`MAX_PARALLEL_PAGES`).
        """
        params: dict[str, str | int] = {"order_type": "all"}
        if type_id is not None:
            params["type_id"] = type_id

        path = f"/markets/{region_id}/orders/"
        first = await self._client.get(path, params=params)
        first.raise_for_status()

        orders: list[MarketOrder] = [
            MarketOrder.model_validate(item) for item in first.json()
        ]
        total_pages = int(first.headers.get("X-Pages", "1"))
        if total_pages <= 1:
            return orders

        semaphore = asyncio.Semaphore(MAX_PARALLEL_PAGES)

        async def fetch_page(page: int) -> list[MarketOrder]:
            async with semaphore:
                resp = await self._client.get(path, params={**params, "page": page})
                resp.raise_for_status()
                return [MarketOrder.model_validate(item) for item in resp.json()]

        pages = await asyncio.gather(
            *(fetch_page(p) for p in range(2, total_pages + 1))
        )
        for page_orders in pages:
            orders.extend(page_orders)
        return orders
