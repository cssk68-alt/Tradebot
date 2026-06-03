"""Configuration via pydantic-settings (loads from environment / .env)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    anthropic_api_key: str = ""

    # Execution
    mode: str = "paper"  # "paper" | "live"
    bankroll: float = 1000.0

    # Live creds (Polymarket / Polygon) — only needed in live mode
    polymarket_private_key: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # Strategy knobs
    kelly_fraction: float = 0.25
    max_trade_pct: float = 0.05
    max_exposure_pct: float = 0.5
    min_liquidity: float = 500.0
    min_volume_24h: float = 1000.0
    edge_threshold: float = 0.05
    confidence_threshold: float = 0.6
    brain_weight: float = 0.3
    brain_veto_threshold: float = 0.35
    max_slippage: float = 0.02
    min_days_to_resolution: float = 1.0
    max_days_to_resolution: float = 30.0

    # Paths
    db_path: Path = DATA_DIR / "tradebot.db"
    brain_path: Path = DATA_DIR / "brain.npz"
    dashboard_path: Path = ROOT / "docs" / "dashboard" / "state.json"

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key.strip())


def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
