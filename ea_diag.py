"""
EA diagnostic — run on the backend box while the EA App is open + logged in.

Dumps every `authorization=Bearer ` JWT in EADesktop memory with its decoded
claims (so we can see how many there are and which one is the EADM/novafusion
token), and searches for the machineHash `.dlf` license file.

    python ea_diag.py

Prints claims (iss/aud/client_id/scope) but never the token signature.
"""
import base64
import json
import os
import re
from pathlib import Path


def b64json(seg: str):
    seg += "=" * (-len(seg) % 4)
    return json.loads(base64.urlsafe_b64decode(seg.encode()))


def decode_jwt(tok: str):
    parts = tok.split(".")
    if len(parts) < 2:
        return None, None
    try:
        return b64json(parts[0]), b64json(parts[1])
    except Exception:
        return None, None


def dump_jwts():
    import pymem
    from pymem.pattern import pattern_scan_all
    marker = b"authorization=Bearer "
    pm = pymem.Pymem("EADesktop.exe")
    addrs = pattern_scan_all(pm.process_handle, marker, return_multiple=True) or []
    print(f"=== {len(addrs)} 'authorization=Bearer ' markers in memory ===")
    seen = {}
    for a in addrs:
        try:
            blob = pm.read_bytes(a + len(marker), 4000)
        except Exception:
            continue
        m = re.match(rb"[A-Za-z0-9=._\-]+", blob)
        if not m:
            continue
        tok = m.group(0).decode("latin-1")
        if not tok.startswith("eyJ"):
            continue
        fp = tok[:32]
        if fp in seen:
            continue
        seen[fp] = True
        hdr, pl = decode_jwt(tok)
        print(f"\nJWT  len={len(tok)}  head={tok[:14]}...")
        if hdr:
            print(f"   hdr: kid={hdr.get('kid')} alg={hdr.get('alg')}")
        if pl:
            print(f"   iss={pl.get('iss')}")
            print(f"   aud={pl.get('aud')}  azp/client={pl.get('azp') or pl.get('client_id')}")
            print(f"   scope={str(pl.get('scope') or pl.get('scp'))[:90]}")
            print(f"   sub/pid={pl.get('sub') or pl.get('pid_id')}  exp={pl.get('exp')}")
    pm.close_process()
    print(f"\n=> {len(seen)} unique JWT(s) total")


def find_machine_hash():
    print("\n=== machineHash / .dlf search ===")
    roots = [
        r"C:\ProgramData\Electronic Arts",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Electronic Arts"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "EA Desktop"),
    ]
    found_any = False
    for root in roots:
        p = Path(root)
        if not p.exists():
            print(f"  MISSING: {root}")
            continue
        dlfs = list(p.rglob("*.dlf"))
        print(f"  {root}: {len(dlfs)} .dlf")
        for d in dlfs[:8]:
            print(f"     {d}")
            found_any = True
    if not found_any:
        print("  -> No .dlf anywhere. The RDP's EA App has no license machineHash")
        print("     (usually created when a Denuvo game is activated on the box).")


if __name__ == "__main__":
    try:
        dump_jwts()
    except Exception as e:
        print("JWT dump failed:", e)
    try:
        find_machine_hash()
    except Exception as e:
        print("machineHash search failed:", e)
