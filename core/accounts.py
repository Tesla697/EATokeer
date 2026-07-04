"""
EA account pool (mirror of UbiTokeer/core/accounts.py).

An account is one EA login whose session has been snapshotted on THIS machine:
    {
      "name": "Main EA",
      "email": "user@example.com",          # display only
      "snapshot": "snapshots/Main EA",       # saved EA Desktop session folder
      "content_ids": ["16425677"],           # games this account owns
      "daily_limit": 5,
      "track_quota": true
    }
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("eatokeer")

ACCOUNTS_PATH = Path(__file__).parent.parent / "accounts.json"


def read_accounts() -> list[dict]:
    if not ACCOUNTS_PATH.exists():
        logger.error("accounts.json not found")
        return []
    try:
        data = json.loads(ACCOUNTS_PATH.read_text())
        return data.get("accounts", [])
    except Exception as e:
        logger.error(f"Failed to load accounts.json: {e}")
        return []


def write_accounts(accounts: list[dict]) -> None:
    ACCOUNTS_PATH.write_text(json.dumps({"accounts": accounts}, indent=2))


def get_accounts_for_content_id(content_id: str) -> list[dict]:
    """All accounts that own the given content_id (regardless of quota)."""
    return [a for a in read_accounts() if content_id in a.get("content_ids", [])]


def has_any_account_for_content_id(content_id: str) -> bool:
    return len(get_accounts_for_content_id(content_id)) > 0


def get_account_for_content_id(content_id: str, quota) -> dict | None:
    """First account owning content_id that still has quota."""
    for acc in read_accounts():
        if content_id in acc.get("content_ids", []):
            if not acc.get("track_quota", True) or quota.can_generate(acc["name"], content_id):
                return acc
    return None
