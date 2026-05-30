"""Application configuration for the EVE Market Analyzer.

Centralizes the EVE ESI base URL, request defaults, the local cache directory,
and the path to the bundled CCP static-data export (jsonl). Construct a fresh
``Config()`` to override any default; import ``config`` for the shared, process-
wide instance.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Config(BaseModel):
    """Runtime configuration. Immutable once constructed."""

    model_config = ConfigDict(frozen=True)

    esi_base_url: str = Field(default="https://esi.evetech.net/latest")
    esi_user_agent: str = Field(
        default="eve-market-analyzer/0.1.0 (+contact: set-me@example.com)",
    )
    request_timeout_seconds: float = Field(default=60.0, ge=1.0)

    cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "eve-market-analyzer",
    )

    static_data_dir: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "eve-online-static-data-3351823-jsonl",
    )

    def ensure_cache_dir(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir


config = Config()
