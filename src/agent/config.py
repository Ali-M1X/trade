"""Load config.yaml and secrets from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path or os.environ.get("AGENT_CONFIG") or DEFAULT_PATH)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    coingecko_api_key: str | None

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_secrets(env: dict | None = None) -> Secrets:
    env = os.environ if env is None else env
    return Secrets(
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or None,
        coingecko_api_key=env.get("COINGECKO_API_KEY") or None,
    )
