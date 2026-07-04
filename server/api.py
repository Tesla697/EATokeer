"""
EATokeer FastAPI backend.

HTTP contract kept identical to the standalone ea_ticket_server.py so the
existing danny EA cog talks to it unchanged:
  POST /request   {"content_id": "...", "token_req": "..."}  -> {"job_id","status"}
  GET  /job/{id}  -> {"status", "token"?, "error"?}
  GET  /quota                 -> {content_id: {total_remaining, total, ...}}
  GET  /quota/{content_id}    -> {content_id, remaining, total, resets_in}
  GET  /status /health
"""

import logging
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.job_queue import BusyError, JobQueue
from core.quota import QuotaExceededError

logger = logging.getLogger("eatokeer")

app = FastAPI(title="EATokeer", docs_url=None, redoc_url=None)

_queue: Optional[JobQueue] = None
_api_key: str = ""


def set_queue(queue: JobQueue) -> None:
    global _queue
    _queue = queue


def set_api_key(key: str) -> None:
    global _api_key
    _api_key = key or ""


def _check_key(x_api_key: Optional[str]) -> Optional[JSONResponse]:
    if _api_key and x_api_key != _api_key:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})
    return None


class JobRequest(BaseModel):
    token_req: str
    content_id: str = ""
    uplay_id: str = ""  # accepted as an alias so the Ubi-style cog posts unchanged


@app.post("/request")
def submit_request(body: JobRequest, x_api_key: Optional[str] = Header(default=None)):
    denied = _check_key(x_api_key)
    if denied:
        return denied
    content_id = body.content_id or body.uplay_id or ""
    logger.info(f"API: POST /request content_id={content_id}")
    if not body.token_req.strip():
        return JSONResponse(status_code=400, content={"error": "Empty token_req"})
    try:
        job = _queue.submit(content_id, body.token_req)
        return {"job_id": job.id, "status": job.status.value}
    except QuotaExceededError as e:
        logger.warning(f"API: Quota exceeded — {e}")
        return JSONResponse(status_code=429, content={"error": str(e)})
    except BusyError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"API: Unexpected error — {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get("/job/{job_id}")
def get_job(job_id: str, x_api_key: Optional[str] = Header(default=None)):
    denied = _check_key(x_api_key)
    if denied:
        return denied
    job = _queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.get("/quota")
def get_quota():
    return _queue.get_quota_summary()


@app.get("/quota/{content_id}")
def get_quota_one(content_id: str):
    return _queue.get_quota_simple(content_id)


@app.get("/status")
def get_status():
    state = _queue.get_state()
    current = state["current"]
    return {
        "status": "busy" if current else "idle",
        "queue_size": (1 if state["pending"] else 0),
        "current_job": current,
        "pending_job": state["pending"],
    }


@app.get("/health")
def health():
    return {"success": True, "service": "eatokeer", "status": "ok", "time": int(time.time())}
