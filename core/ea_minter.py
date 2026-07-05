"""
EA GameToken minter — the novafusion license call + supporting bits, ported
from the standalone ea_ticket_server.py (decompiled AnadiusLogic).

Reads the bearer token from the CURRENTLY logged-in EADesktop.exe, so callers
must swap the desired account in first (see account_manager). Everything else
(machineHash, AES, novafusion) is account-independent on a given box.
"""

import base64
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger("eatokeer")

# --- Constants (verbatim from AnadiusLogic) -------------------------------- #
LICENSE_ENDPOINT = "https://proxy.novafusion.ea.com/licenses"
ORIGIN_AES_KEY = base64.b64decode("QTJyLdCC77DcZFfFdmjKCQ==")  # AES-128-CBC, zero IV, PKCS7
ORIGIN_HEADERS = {
    "User-Agent": "EACTransaction",
    "X-Requester-Id": "Origin Online Activation",
}
BEARER_MARKER = b"authorization=Bearer "
LICENSE_NS = "{http://ea.com/license}"
MAX_TOKENS_PER_DAY = 5
EA_PROCESS = "EADesktop.exe"

LICENSE_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / \
    "Electronic Arts" / "EA Services" / "License"
DATA_FILE = Path(__file__).parent.parent / "ea_token_data.json"

# Denuvo request line: <blob>|<engine>|<contentId>
TICKET_RE = re.compile(
    r"((?:[A-Za-z0-9_\-]{4}){40,}(?:[A-Za-z0-9\-_=]{2}==|[A-Za-z0-9\-_=]{3}=)?)"
    r"\|(\d+)\|(\d+)"
)

_data_lock = threading.Lock()


class EAError(Exception):
    """User-facing generation error."""


# --- token_data.json (machine hash cache) ---------------------------------- #

def _load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text("utf-8"))
        except Exception as exc:
            logger.warning("token_data load failed: %s", exc)
    return {"machine_hash": None, "tokens": {}}


def _save_data(data: dict) -> None:
    try:
        DATA_FILE.write_text(json.dumps(data, indent=2), "utf-8")
    except Exception as exc:
        logger.warning("token_data save failed: %s", exc)


# --- AES-128-CBC (zero IV, PKCS7) ------------------------------------------ #

def aes_decrypt(data: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(ORIGIN_AES_KEY), modes.CBC(b"\x00" * 16))
    dec = cipher.decryptor()
    out = dec.update(data) + dec.finalize()
    pad = out[-1] if out else 0
    if 1 <= pad <= 16:
        out = out[:-pad]
    return out


def get_machine_hash() -> str:
    with _data_lock:
        cached = _load_data().get("machine_hash")
    if cached:
        return cached
    if not LICENSE_DIR.exists():
        logger.warning("EA Services License dir not found: %s", LICENSE_DIR)
        return "1"
    for dlf in LICENSE_DIR.glob("*.dlf"):
        try:
            raw = dlf.read_bytes()[65:]
            xml = aes_decrypt(raw).decode("utf-8", "replace")
            root = ET.fromstring(xml)
            el = root.find(f"{LICENSE_NS}MachineHash")
            if el is not None and el.text:
                mh = el.text.strip()
                with _data_lock:
                    d = _load_data()
                    d["machine_hash"] = mh
                    _save_data(d)
                logger.info("Found MachineHash: %s", mh)
                return mh
        except Exception:
            continue
    logger.warning("No valid machine hash found; using default '1'.")
    return "1"


def get_access_token_from_memory() -> str:
    """Read the bearer token from the currently logged-in EADesktop.exe.

    EADesktop holds MANY `authorization=Bearer ` strings in memory; most are
    empty header templates whose next bytes are `\\r\\nCookie: ...` etc. So we
    scan ALL matches, keep only those where a real token starts *immediately*
    after the marker (anchored match — placeholders are skipped), then prefer a
    JWT (`eyJ...`) / the longest candidate.
    """
    try:
        import pymem
        from pymem.pattern import pattern_scan_all
    except ImportError as exc:
        raise RuntimeError("pymem is required (pip install pymem).") from exc

    try:
        pm = pymem.Pymem(EA_PROCESS)
    except Exception as exc:
        raise RuntimeError(
            "Could not open EADesktop.exe. Is the EA App running and logged in?"
        ) from exc
    candidates: list[str] = []
    try:
        addrs = pattern_scan_all(pm.process_handle, BEARER_MARKER, return_multiple=True) or []
        for addr in addrs:
            start = addr + len(BEARER_MARKER)
            # EA JUNO access tokens are large (several KB). Read a big window so
            # the JWT isn't truncated — a cut-off token => AUTHENTICATION_FAILED.
            # Fall back to smaller reads if the window crosses a region boundary.
            blob = None
            for size in (32768, 16384, 8192):
                try:
                    blob = pm.read_bytes(start, size)
                    break
                except Exception:
                    continue
            if not blob:
                continue
            text = blob.decode("latin-1", "replace")
            # anchored: token must start right after "Bearer " so empty
            # "Bearer \r\nCookie:" placeholders yield no match and are skipped.
            m = re.match(r"[a-zA-Z0-9=._\-]+", text)
            if m:
                candidates.append(m.group(0))
    finally:
        try:
            pm.close_process()
        except Exception:
            pass

    if not candidates:
        raise RuntimeError(
            "Could not find an access token in EA App memory (only empty Bearer "
            "placeholders). The EA App may not be fully logged in yet."
        )
    jwts = [t for t in candidates if t.startswith("eyJ")]
    if jwts:
        return max(jwts, key=len)
    longish = [t for t in candidates if len(t) >= 100]
    if longish:
        return max(longish, key=len)
    raise RuntimeError(
        f"Found Bearer markers but only short placeholders (e.g. {candidates[0]!r}); "
        "no valid access token resident yet."
    )


def generate_token(raw_ticket: str, expected_content_id: str = "") -> str:
    """Mint a GameToken for the CURRENTLY active EA account. Raises EAError."""
    m = TICKET_RE.search(raw_ticket or "")
    if not m:
        raise EAError("Bad ticket! The Denuvo request format is incorrect.")
    request_token, engine, content_id = m.group(1), m.group(2), m.group(3)

    if expected_content_id and str(expected_content_id) != content_id:
        raise EAError(
            f"This ticket is for content ID {content_id}, but this game expects "
            f"{expected_content_id}. Wrong game?"
        )

    machine_hash = get_machine_hash()
    access_token = get_access_token_from_memory()

    params = {
        "contentId": content_id,
        "machineHash": machine_hash,
        "ea_eadmtoken": access_token,
        "requestToken": request_token,
        "requestType": engine,
    }

    tries = 1
    while True:
        logger.info("novafusion request for content %s (attempt %d)", content_id, tries)
        resp = requests.get(LICENSE_ENDPOINT, params=params, headers=ORIGIN_HEADERS, timeout=30)
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()

        if ctype.startswith("application/octet-stream"):
            xml = aes_decrypt(resp.content).decode("utf-8", "replace")
            root = ET.fromstring(xml)
            el = root.find(f"{LICENSE_NS}GameToken")
            if el is None or not el.text:
                raise EAError("Decryption OK but no GameToken in the license XML.")
            return el.text.strip()

        if ctype.startswith("application/xml"):
            try:
                root = ET.fromstring(resp.text)
                code = root.get("code")
                cause = None
                fail = root.find("failure")
                if fail is not None:
                    cause = fail.get("cause")
            except Exception:
                code, cause = None, None
            if code == "CG_LIMIT_EXCEEDED":
                raise EAError(f"CG_LIMIT_EXCEEDED: daily activation limit reached ({MAX_TOKENS_PER_DAY}/day).")
            if code == "VALIDATION_FAILED" and cause == "NOT_ENTITLED":
                raise EAError(f"NOT_ENTITLED: this account doesn't own content ID {content_id}.")
            if code == "VALIDATION_FAILED" and cause == "AUTHENTICATION_FAILED":
                raise EAError("AUTHENTICATION_FAILED — the EA App access token may be invalid.")
            raise EAError(f"EA returned an XML error: {resp.text[:200]}")

        if ctype.startswith("text/html") and resp.status_code == 404:
            if tries >= 5:
                raise EAError("Failed 5x — EA servers, the ticket, or the account is the issue.")
            delay = 5 * tries
            logger.info("Generic EA error; retrying in %ds", delay)
            time.sleep(delay)
            tries += 1
            continue

        raise EAError(
            f"Unknown EA response. Status {resp.status_code}, Content-Type {ctype}, "
            f"Body {resp.text[:200]}"
        )


def is_limit_error(exc: Exception) -> bool:
    """True if the error means 'this account is capped' (rotate to next)."""
    return "CG_LIMIT_EXCEEDED" in str(exc)
