# EATokeer

EA GameToken minter backend with multi-account rotation — the EA/Origin sibling
of Tokeer (Steam) and UbiTokeer. Mints EA Denuvo **GameTokens** by reading the
bearer token from a logged-in EA App and calling EA's license server, rotating
between multiple accounts as each hits its daily cap.

FastAPI server + CustomTkinter GUI, same shape as UbiTokeer. Serves the token
API to the Discord bot cog and shows live per-account/per-game quota.

## How it works

EA welds token-minting to the physical machine (a signed device fingerprint),
so tokens can only be minted for the account currently signed into the EA App on
this box. To support multiple accounts on one machine, EATokeer uses a
**TcNo-style session swap**: each account is logged into the EA App once and its
`%LocalAppData%\Electronic Arts\EA Desktop\` session folder is snapshotted. To
switch accounts, it kills the EA App, restores that account's snapshot,
relaunches, and scrapes the fresh bearer token from `EADesktop.exe` memory.

Per request: pick an account that owns the game and still has quota → swap it in
if needed → mint the GameToken → on `CG_LIMIT_EXCEEDED`, rotate to the next
account. Quota is tracked per account per game (default 5/day) and decrements on
every token.

## Requirements

- **Windows** (uses DPAPI + reads EA App process memory)
- The **EA App (EA Desktop)** installed, with the backend accounts able to log in
- Python 3.11+

## Setup (on the backend / RDP box)

```bat
git clone https://github.com/Tesla697/EATokeer.git
cd EATokeer
pip install -r requirements.txt
copy config.example.json config.json
copy accounts.example.json accounts.json
python main.py
```

Then in the GUI:

1. Log an account into the **EA App**.
2. **Config tab → "Save Current EA Account"** → name it and list the content IDs
   (games) it owns. This snapshots the login into `snapshots/<name>/`.
3. Log in the next account and repeat to build the pool.

The API server auto-starts on the port in `config.json` (default **8092**).

## config.json

| key              | meaning                                             |
|------------------|-----------------------------------------------------|
| `port`           | API server port (default 8092)                      |
| `daily_limit`    | tokens per account per game per 24h (default 5)     |
| `api_key`        | optional; if set, clients must send `X-API-Key`     |
| `swap_timeout`   | seconds to wait for a token after an account swap   |
| `ea_desktop_exe` | path to `EADesktop.exe`                             |

## API

| method | path                   | notes                                    |
|--------|------------------------|------------------------------------------|
| POST   | `/request`             | `{content_id, token_req}` → `{job_id}`   |
| GET    | `/job/{id}`            | → `{status, token?, error?}`             |
| GET    | `/quota`               | per-game aggregate remaining             |
| GET    | `/quota/{content_id}`  | one game's remaining + reset time        |
| GET    | `/status` `/health`    |                                          |

## Security

This repo ships **no credentials**. `.gitignore` excludes `snapshots/`,
`accounts.json`, `config.json`, and cached data. **Never commit `snapshots/`** —
those folders contain live EA login sessions (full account access).
