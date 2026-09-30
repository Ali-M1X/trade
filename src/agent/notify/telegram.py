"""Telegram sender. Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID it prints instead."""
from __future__ import annotations

import logging
import sys
import time

import requests

log = logging.getLogger(__name__)
LIMIT = 4096


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Split on line breaks so no message exceeds Telegram's limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class Notifier:
    def __init__(self, cfg: dict, secrets, session=None, out=None, sleep=time.sleep):
        self.cfg = cfg["telegram"]
        self.secrets = secrets
        self.session = session or requests.Session()
        self.out = out or sys.stdout
        self.sleep = sleep
        self.sent: list[str] = []

    @property
    def dry_run(self) -> bool:
        return not self.secrets.telegram_enabled

    def send(self, text: str) -> bool:
        self.sent.append(text)
        if self.dry_run:
            print(f"----- telegram (dry-run) -----\n{text}\n", file=self.out)
            return True
        ok = True
        for part in split(text):
            ok = self._post(part) and ok
        return ok

    def _post(self, text: str) -> bool:
        url = f"{self.cfg['api_base']}/bot{self.secrets.telegram_bot_token}/sendMessage"
        body = {"chat_id": self.secrets.telegram_chat_id, "text": text,
                "disable_web_page_preview": True}
        if self.cfg.get("parse_mode"):
            body["parse_mode"] = self.cfg["parse_mode"]
        for attempt in range(3):
            try:
                r = self.session.post(url, json=body, timeout=self.cfg["timeout_s"])
            except requests.RequestException as e:
                log.warning("telegram send failed: %s", e)
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                self.sleep(float(wait))
                continue
            if r.status_code != 200:
                # never log the URL: it contains the bot token
                log.warning("telegram HTTP %s: %s", r.status_code, r.text[:200])
                return False
            return True
        return False
