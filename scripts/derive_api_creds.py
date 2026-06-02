"""Standalone helper: derive Polymarket API credentials from your wallet key.

Usage:  POLYMARKET_PRIVATE_KEY=0x... python scripts/derive_api_creds.py
(or set it in .env first). Prints the lines to paste into your .env.
"""
from tradebot.config import get_settings
from tradebot.exchange.polymarket import derive_api_creds
from tradebot.log import get_logger


def main() -> None:
    log = get_logger("derive")
    settings = get_settings()
    if not settings.polymarket_private_key:
        log.error("Set POLYMARKET_PRIVATE_KEY (env or .env) first.")
        return
    creds = derive_api_creds(settings, log)
    if creds:
        for k, v in creds.items():
            print(f"POLYMARKET_{k.upper()}={v}")


if __name__ == "__main__":
    main()
