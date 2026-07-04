"""
EA worker — the CliWorker equivalent for EATokeer.

generate() swaps the desired account into the EA App (if not already active),
then mints a GameToken from that account's live session.
"""

import logging

from core import account_manager, ea_minter

logger = logging.getLogger("eatokeer")


class EaWorkerError(Exception):
    pass


class EaWorker:
    def __init__(self, swap_timeout: int = 60):
        self._swap_timeout = swap_timeout

    def generate(self, account: dict, content_id: str, token_req: str) -> dict:
        """
        Ensure `account` is the active EA login, then mint its GameToken.
        Returns {"game_token": str}. Raises on failure (queue rotates accounts).
        """
        logger.info(f"Generating for content {content_id} via account '{account['name']}'")
        account_manager.ensure_active(account, self._swap_timeout)
        token = ea_minter.generate_token(token_req, content_id)
        return {"game_token": token}
