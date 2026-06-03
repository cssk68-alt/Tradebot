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

    # LLM — provider-agnostic. Pick with LLM_PROVIDER; only the matching key is
    # needed. DeepSeek is the default for the paper phase (~10x cheaper).
    llm_provider: str = "deepseek"  # "anthropic" | "deepseek"
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    deepseek_model: str = "deepseek-chat"

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

    # Short-horizon scalping (close within minutes at the real price, no waiting
    # for the event). "scalp" = exit on price; "resolve" = hold to resolution.
    strategy: str = "scalp"
    max_hold_seconds: float = 300.0  # close a scalp after this long (5 min default)
    take_profit: float = 0.02  # close in profit once price moved this much our way
    stop_loss: float = 0.03  # cap the loss once price moved this much against us
    min_net_profit: float = 0.005  # required profit AFTER spread to even open a scalp
    min_spread_cost: float = 0.01  # floor for round-trip spread charged on a paper exit

    # Paths
    db_path: Path = DATA_DIR / "tradebot.db"
    brain_path: Path = DATA_DIR / "brain.npz"
    dashboard_path: Path = ROOT / "docs" / "dashboard" / "state.json"

    @property
    def llm_api_key(self) -> str:
        """The API key for the currently selected provider."""
        keys = {"anthropic": self.anthropic_api_key, "deepseek": self.deepseek_api_key}
        return keys.get(self.llm_provider.lower(), "").strip()

    @property
    def has_llm(self) -> bool:
        """True iff the selected provider has a key configured."""
        return bool(self.llm_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key.strip())


def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    s = Settings()
    _apply_user_config(s)
    return s


def _apply_user_config(s: Settings) -> None:
    """Override strategy knobs from data/config.json if it exists (written by the local UI)."""
    import json

    cfg_path = DATA_DIR / "config.json"
    if not cfg_path.exists():
        return
    try:
        overrides = json.loads(cfg_path.read_text())
    except Exception:
        return
    for key, val in overrides.items():
        if hasattr(s, key):
            try:
                setattr(s, key, val)
            except Exception:
                pass
