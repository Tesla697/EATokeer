"""
Job queue + rotation (mirror of UbiTokeer/core/job_queue.py).

On each request it picks an account that owns the game and still has quota;
if generation fails (e.g. CG_LIMIT_EXCEEDED), it rotates to the next eligible
account — which triggers a TcNo-style EA session swap in the worker.
"""

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from core.accounts import (
    get_account_for_content_id,
    get_accounts_for_content_id,
    has_any_account_for_content_id,
)
from core.ea_worker import EaWorker
from core.job import Job, JobStatus
from core.quota import QuotaExceededError, QuotaTracker

logger = logging.getLogger("eatokeer")


class BusyError(Exception):
    pass


class JobQueue:
    def __init__(self, config: dict, on_update: Optional[Callable] = None):
        self._config = config
        self._on_update = on_update
        self._lock = threading.Lock()
        self._current: Optional[Job] = None
        self._pending: Optional[Job] = None
        self._jobs: dict[str, Job] = {}
        self._condition = threading.Condition(self._lock)
        self._quota = QuotaTracker(daily_limit=config.get("daily_limit", 5))
        self._worker = EaWorker(swap_timeout=config.get("swap_timeout", 60))
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Job queue started")

    def submit(self, content_id: str, token_req: str) -> Job:
        if not has_any_account_for_content_id(content_id):
            raise ValueError(f"No account assigned to content_id={content_id}")

        account = get_account_for_content_id(content_id, self._quota)
        if not account:
            raise QuotaExceededError(
                f"Daily token limit reached for all accounts owning content_id={content_id}"
            )

        with self._lock:
            if self._current is not None and self._pending is not None:
                raise BusyError("Queue is full. Try again later.")
            job = Job(
                content_id=content_id,
                account_name=account["name"],
                snapshot=account.get("snapshot", ""),
                token_req=token_req,
            )
            self._jobs[job.id] = job
            self._pending = job
            self._condition.notify_all()

        self._notify_update()
        logger.info(f"Job {job.id} submitted: content_id={content_id}, account={account['name']}")
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_state(self) -> dict:
        with self._lock:
            return {
                "current": self._current.to_dict() if self._current else None,
                "pending": self._pending.to_dict() if self._pending else None,
            }

    def _worker_loop(self) -> None:
        while self._running:
            with self._condition:
                while self._pending is None and self._running:
                    self._condition.wait(timeout=1.0)
                if not self._running:
                    break
                job = self._pending
                self._pending = None
                self._current = job
            self._notify_update()
            self._process_job(job)
            with self._lock:
                self._current = None
            self._notify_update()

    def _process_job(self, job: Job) -> None:
        logger.info(f"Processing job {job.id}: content_id={job.content_id}, account={job.account_name}")
        job.status = JobStatus.PROCESSING
        self._notify_update()

        # Primary account first, then any other eligible account for this game.
        all_accounts = get_accounts_for_content_id(job.content_id)
        primary = next((a for a in all_accounts if a["name"] == job.account_name), None)
        accounts_to_try = [primary] if primary else []
        for acc in all_accounts:
            if acc["name"] != job.account_name and (
                not acc.get("track_quota", True) or self._quota.can_generate(acc["name"], job.content_id)
            ):
                accounts_to_try.append(acc)

        last_error = None
        for attempt, acc in enumerate(accounts_to_try):
            if acc is None:
                continue
            if attempt > 0:
                logger.info(f"Job {job.id}: rotating to account '{acc['name']}'...")
                job.account_name = acc["name"]
                job.snapshot = acc.get("snapshot", "")

            try:
                result = self._worker.generate(acc, job.content_id, job.token_req)
                job.game_token = result["game_token"]
                job.status = JobStatus.DONE
                job.finished_at = datetime.utcnow()
                if acc.get("track_quota", True):
                    self._quota.record(acc["name"], job.content_id)
                logger.info(f"Job {job.id} completed (account '{acc['name']}')")
                self._notify_update()
                return
            except Exception as e:
                last_error = e
                logger.warning(f"Job {job.id}: account '{acc['name']}' failed: {e}")
                continue

        job.status = JobStatus.FAILED
        job.error = str(last_error) if last_error else "All accounts failed"
        job.finished_at = datetime.utcnow()
        logger.error(f"Job {job.id} failed on all accounts: {job.error}")
        self._notify_update()

    def get_quota_simple(self, content_id: str) -> dict:
        return self._quota.get_simple(content_id, get_accounts_for_content_id(content_id))

    def get_quota_summary(self) -> dict:
        from core.accounts import read_accounts
        return self._quota.get_summary(read_accounts())

    def _notify_update(self) -> None:
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                pass

    def update_config(self, config: dict) -> None:
        self._config = config

    def shutdown(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        logger.info("Job queue shut down")
