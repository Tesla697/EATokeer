import uuid
from datetime import datetime
from enum import Enum
from typing import Optional


class JobStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Job:
    def __init__(self, content_id: str, account_name: str, snapshot: str, token_req: str):
        self.id = uuid.uuid4().hex[:8]
        self.content_id = content_id
        self.account_name = account_name
        self.snapshot = snapshot
        self.token_req = token_req
        self.status = JobStatus.QUEUED
        self.game_token: Optional[str] = None
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow()
        self.finished_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = {
            "job_id": self.id,
            "content_id": self.content_id,
            "account_name": self.account_name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }
        # `token` key kept identical to the standalone ea_ticket_server so the
        # existing danny EA cog reads the result unchanged.
        if self.game_token:
            d["token"] = self.game_token
        if self.error:
            d["error"] = self.error
        if self.finished_at:
            d["finished_at"] = self.finished_at.isoformat()
        return d
