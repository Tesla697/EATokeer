import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import customtkinter as ctk

CONFIG_PATH = Path(__file__).parent.parent / "config.json"
ACCOUNTS_PATH = Path(__file__).parent.parent / "accounts.json"
GAME_NAMES_PATH = Path(__file__).parent.parent / "game_names.json"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

STATUS_COLORS = {
    "idle": "#4caf50",
    "busy": "#ffa500",
    "error": "#f44336",
    "stopped": "#888888",
}

logger = logging.getLogger("eatokeer")


class GuiLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self._q = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._q.put_nowait(record)
        except queue.Full:
            pass


class EATokeerApp(ctk.CTk):
    def __init__(self, config: dict, on_save_config: Callable[[dict], None],
                 on_toggle_server: Callable[[bool], None]):
        super().__init__()
        self._config = config
        self._on_save_config = on_save_config
        self._on_toggle_server = on_toggle_server
        self._server_running = False
        self._log_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._queue_state: dict = {"current": None, "pending": None}
        self._queue_state_lock = threading.Lock()
        self._quota_tracker = None

        self.title("EATokeer Backend")
        self.geometry("860x680")
        self.minsize(740, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll_logs()
        self._poll_state()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        top = ctk.CTkFrame(self, height=46, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        self._status_dot = ctk.CTkLabel(top, text="●", font=("Segoe UI", 18), width=24)
        self._status_dot.pack(side="left", padx=(12, 4), pady=8)
        self._status_label = ctk.CTkLabel(top, text="IDLE", font=("Segoe UI", 13, "bold"))
        self._status_label.pack(side="left", padx=(0, 16))

        port = self._config.get("port", 8092)
        self._server_info = ctk.CTkLabel(top, text=f"Server: 0.0.0.0:{port}",
                                         font=("Segoe UI", 12), text_color="#aaaaaa")
        self._server_info.pack(side="left", padx=4)

        self._active_info = ctk.CTkLabel(top, text="Active: —",
                                         font=("Segoe UI", 12), text_color="#7fa7ff")
        self._active_info.pack(side="left", padx=12)

        self._toggle_btn = ctk.CTkButton(top, text="Start Server", width=110, height=30,
                                         command=self._toggle_server)
        self._toggle_btn.pack(side="right", padx=12, pady=8)

        self._tabs = ctk.CTkTabview(self)
        self._tabs.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self._tabs.add("Dashboard")
        self._tabs.add("Quota")
        self._tabs.add("Config")
        self._build_dashboard(self._tabs.tab("Dashboard"))
        self._build_quota_tab(self._tabs.tab("Quota"))
        self._build_config_tab(self._tabs.tab("Config"))
        self._set_status("idle")

    def _build_dashboard(self, parent) -> None:
        info_row = ctk.CTkFrame(parent)
        info_row.pack(fill="x", padx=4, pady=(6, 4))

        job_frame = ctk.CTkFrame(info_row)
        job_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        ctk.CTkLabel(job_frame, text="Current Job", font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=10, pady=(8, 2))
        self._lbl_content_id = self._info_row(job_frame, "Content", "—")
        self._lbl_account = self._info_row(job_frame, "Account", "—")
        self._lbl_job_status = self._info_row(job_frame, "Status", "—")

        queue_frame = ctk.CTkFrame(info_row, width=180)
        queue_frame.pack(side="right", fill="y", padx=(4, 0))
        queue_frame.pack_propagate(False)
        ctk.CTkLabel(queue_frame, text="Queue", font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=10, pady=(8, 2))
        self._lbl_pending = self._info_row(queue_frame, "Pending", "empty")

        log_frame = ctk.CTkFrame(parent)
        log_frame.pack(fill="both", expand=True, padx=4, pady=(4, 4))
        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkLabel(log_header, text="Logs", font=("Segoe UI", 12, "bold")).pack(side="left")
        ctk.CTkButton(log_header, text="Clear", width=60, height=24,
                     command=self._clear_logs).pack(side="right")
        self._log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 11), wrap="word",
                                       state="disabled", activate_scrollbars=True)
        self._log_box.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._log_box._textbox.tag_configure("DEBUG", foreground="#888888")
        self._log_box._textbox.tag_configure("INFO", foreground="#ffffff")
        self._log_box._textbox.tag_configure("WARNING", foreground="#f0c040")
        self._log_box._textbox.tag_configure("ERROR", foreground="#ff5555")
        self._log_box._textbox.tag_configure("CRITICAL", foreground="#ff0000")

    # -------------------------------------------------------------- Config --
    def _build_config_tab(self, parent) -> None:
        acc_header = ctk.CTkFrame(parent, fg_color="transparent")
        acc_header.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(acc_header, text="EA Accounts", font=("Segoe UI", 12, "bold")).pack(side="left")

        btn_frame = ctk.CTkFrame(acc_header, fg_color="transparent")
        btn_frame.pack(side="right")
        ctk.CTkButton(btn_frame, text="+ Save Current EA Account", width=190, height=26,
                     fg_color="#2e7d32", hover_color="#1b5e20",
                     command=self._save_current_account_dialog).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Save Table", width=90, height=26,
                     command=self._save_accounts).pack(side="left", padx=4)

        hint = ("Log the account into the EA App first, then click "
                "“Save Current EA Account” — it snapshots that login so it "
                "can be swapped in later. Assign the games (content IDs) it owns.")
        ctk.CTkLabel(parent, text=hint, font=("Segoe UI", 10), text_color="#999999",
                     wraplength=800, justify="left").pack(anchor="w", padx=10, pady=(0, 4))

        self._accounts_frame = ctk.CTkScrollableFrame(parent, height=200)
        self._accounts_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        for c, w in enumerate([1, 2, 2, 2, 1, 1]):
            self._accounts_frame.grid_columnconfigure(c, weight=w)
        self._account_rows: list[dict] = []
        self._render_accounts()

        ctk.CTkFrame(parent, height=1, fg_color="#444444").pack(fill="x", padx=10, pady=6)

        game_header = ctk.CTkFrame(parent, fg_color="transparent")
        game_header.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(game_header, text="Game Names (content_id → name)",
                     font=("Segoe UI", 12, "bold")).pack(side="left")
        ctk.CTkButton(game_header, text="Save Game Names", width=140, height=26,
                     command=self._save_game_names).pack(side="right")
        self._game_names_box = ctk.CTkTextbox(parent, height=70, font=("Consolas", 11))
        self._game_names_box.pack(fill="x", padx=10, pady=(0, 4))
        self._game_names_box.insert("0.0", json.dumps(self._load_game_names(), indent=2))

        ctk.CTkFrame(parent, height=1, fg_color="#444444").pack(fill="x", padx=10, pady=6)

        ctk.CTkLabel(parent, text="Settings", font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=10, pady=(0, 6))
        settings = ctk.CTkFrame(parent, fg_color="transparent")
        settings.pack(fill="x", padx=10)

        port_row = ctk.CTkFrame(settings, fg_color="transparent")
        port_row.pack(fill="x", pady=3)
        ctk.CTkLabel(port_row, text="Port:", width=160, anchor="w").pack(side="left")
        self._entry_port = ctk.CTkEntry(port_row, width=80)
        self._entry_port.insert(0, str(self._config.get("port", 8092)))
        self._entry_port.pack(side="left")

        to_row = ctk.CTkFrame(settings, fg_color="transparent")
        to_row.pack(fill="x", pady=3)
        ctk.CTkLabel(to_row, text="Swap Timeout (s):", width=160, anchor="w").pack(side="left")
        self._entry_timeout = ctk.CTkEntry(to_row, width=80)
        self._entry_timeout.insert(0, str(self._config.get("swap_timeout", 60)))
        self._entry_timeout.pack(side="left")

        ctk.CTkButton(parent, text="Save Config", width=120,
                     command=self._save_config).pack(anchor="e", padx=10, pady=10)

    def _render_accounts(self) -> None:
        for w in self._accounts_frame.winfo_children():
            w.destroy()
        self._account_rows.clear()
        for col, text in enumerate(["Name", "Email", "Snapshot", "Content IDs", "Limit", ""]):
            ctk.CTkLabel(self._accounts_frame, text=text, font=("Segoe UI", 11, "bold")).grid(
                row=0, column=col, sticky="w", padx=6, pady=2)

        for i, acc in enumerate(self._load_accounts(), start=1):
            name_e = ctk.CTkEntry(self._accounts_frame, width=110)
            name_e.insert(0, acc.get("name", ""))
            name_e.grid(row=i, column=0, sticky="w", padx=6, pady=2)

            email_e = ctk.CTkEntry(self._accounts_frame, width=150)
            email_e.insert(0, acc.get("email", ""))
            email_e.grid(row=i, column=1, sticky="w", padx=6, pady=2)

            ctk.CTkLabel(self._accounts_frame, text=acc.get("snapshot", ""), anchor="w",
                         text_color="#888888").grid(row=i, column=2, sticky="w", padx=6, pady=2)

            cid_e = ctk.CTkEntry(self._accounts_frame, width=150)
            cid_e.insert(0, ", ".join(acc.get("content_ids", [])))
            cid_e.grid(row=i, column=3, sticky="w", padx=6, pady=2)

            lim_e = ctk.CTkEntry(self._accounts_frame, width=50)
            lim_e.insert(0, str(acc.get("daily_limit", 5)))
            lim_e.grid(row=i, column=4, sticky="w", padx=6, pady=2)

            ctk.CTkButton(self._accounts_frame, text="✕", width=30, height=24,
                         fg_color="#f44336", hover_color="#d32f2f",
                         command=lambda n=acc["name"]: self._remove_account(n)).grid(
                row=i, column=5, padx=6, pady=2)

            self._account_rows.append({
                "orig_name": acc["name"], "name_e": name_e, "email_e": email_e,
                "cid_e": cid_e, "lim_e": lim_e,
            })

    def _save_current_account_dialog(self) -> None:
        name = (ctk.CTkInputDialog(text="Name for the CURRENTLY logged-in EA account:",
                                   title="Save EA Account").get_input() or "").strip()
        if not name:
            return
        accounts = self._load_accounts()
        if any(a["name"] == name for a in accounts):
            logger.warning(f"Account '{name}' already exists — pick a unique name.")
            return
        email = (ctk.CTkInputDialog(text="Email (display only, optional):",
                                    title="Email").get_input() or "").strip()
        cids = (ctk.CTkInputDialog(text="Content IDs this account owns (comma-separated):",
                                   title="Content IDs").get_input() or "").strip()
        content_ids = [c.strip() for c in cids.split(",") if c.strip()]

        logger.info(f"Snapshotting current EA session as '{name}' — EA App will restart...")
        self._toggle_busy_snapshot(True)

        def worker():
            from core import account_manager
            snapshot_rel = f"snapshots/{name}"
            try:
                account_manager.snapshot_current(snapshot_rel)
                account_manager.set_active(name)
                account_manager.start_ea()  # bring the user's EA App back up
                accounts.append({
                    "name": name, "email": email, "snapshot": snapshot_rel,
                    "content_ids": content_ids, "daily_limit": 5, "track_quota": True,
                })
                self._write_accounts(accounts)
                logger.info(f"Saved EA account '{name}' ({snapshot_rel}).")
            except Exception as e:
                logger.error(f"Failed to save account '{name}': {e}")
            finally:
                self.after(0, lambda: (self._render_accounts(), self._toggle_busy_snapshot(False)))

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_busy_snapshot(self, busy: bool) -> None:
        self._set_status("busy" if busy else ("idle" if self._server_running else "stopped"),
                         "SNAPSHOTTING" if busy else None)

    def _remove_account(self, name: str) -> None:
        self._write_accounts([a for a in self._load_accounts() if a["name"] != name])
        self._render_accounts()
        logger.info(f"Account '{name}' removed (snapshot folder left on disk).")

    def _save_accounts(self) -> None:
        accounts = self._load_accounts()
        by_name = {a["name"]: a for a in accounts}
        for row in self._account_rows:
            acc = by_name.get(row["orig_name"])
            if not acc:
                continue
            acc["name"] = row["name_e"].get().strip() or acc["name"]
            acc["email"] = row["email_e"].get().strip()
            acc["content_ids"] = [c.strip() for c in row["cid_e"].get().split(",") if c.strip()]
            try:
                acc["daily_limit"] = int(row["lim_e"].get().strip())
            except ValueError:
                acc["daily_limit"] = 5
        self._write_accounts(accounts)
        self._render_accounts()
        logger.info("Accounts saved")

    def _load_accounts(self) -> list[dict]:
        if ACCOUNTS_PATH.exists():
            try:
                return json.loads(ACCOUNTS_PATH.read_text()).get("accounts", [])
            except Exception:
                pass
        return []

    def _write_accounts(self, accounts: list[dict]) -> None:
        ACCOUNTS_PATH.write_text(json.dumps({"accounts": accounts}, indent=2))

    def _load_game_names(self) -> dict:
        if GAME_NAMES_PATH.exists():
            try:
                return json.loads(GAME_NAMES_PATH.read_text())
            except Exception:
                pass
        return {}

    def _save_game_names(self) -> None:
        try:
            data = json.loads(self._game_names_box.get("0.0", "end").strip())
            GAME_NAMES_PATH.write_text(json.dumps(data, indent=2))
            logger.info("Game names saved")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in game names: {e}")

    def _save_config(self) -> None:
        try:
            self._config["port"] = int(self._entry_port.get().strip())
        except ValueError:
            pass
        try:
            self._config["swap_timeout"] = int(self._entry_timeout.get().strip())
        except ValueError:
            pass
        CONFIG_PATH.write_text(json.dumps(self._config, indent=2))
        self._on_save_config(self._config)
        logger.info("Config saved and reloaded")
        self._server_info.configure(text=f"Server: 0.0.0.0:{self._config.get('port', 8092)}")

    # --------------------------------------------------------------- Quota --
    def _build_quota_tab(self, parent) -> None:
        self._quota_scroll = ctk.CTkScrollableFrame(parent)
        self._quota_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        ctk.CTkLabel(self._quota_scroll, text="Loading quota data...",
                     text_color="#aaaaaa").pack(pady=20)
        self._quota_structure_key: tuple = ()
        self._quota_labels: dict[tuple[str, str], dict] = {}

    def set_quota_tracker(self, tracker) -> None:
        self._quota_tracker = tracker
        self._poll_quota()

    def _poll_quota(self) -> None:
        if self._quota_tracker:
            self._refresh_quota_panel()
        self.after(1000, self._poll_quota)

    def _structure_key(self, content_map: dict) -> tuple:
        parts = []
        for cid in sorted(content_map):
            for name in content_map[cid]:
                parts.append((cid, name))
        return tuple(parts)

    def _refresh_quota_panel(self) -> None:
        accounts = self._load_accounts()
        content_map: dict[str, list[str]] = {}
        for acc in accounts:
            for cid in acc.get("content_ids", []):
                content_map.setdefault(cid, []).append(acc["name"])

        if not content_map:
            if self._quota_structure_key != ():
                self._quota_structure_key = ()
                self._quota_labels.clear()
                for w in self._quota_scroll.winfo_children():
                    w.destroy()
                ctk.CTkLabel(self._quota_scroll, text="No accounts assigned to any game.",
                             text_color="#aaaaaa").pack(pady=20)
            return

        new_key = self._structure_key(content_map)
        game_names = self._load_game_names()
        if new_key != self._quota_structure_key:
            self._quota_structure_key = new_key
            self._quota_labels.clear()
            for w in self._quota_scroll.winfo_children():
                w.destroy()
            self._build_quota_widgets(accounts, content_map, game_names)
            return

        now = time.time()
        for cid, names in content_map.items():
            for name in names:
                labels = self._quota_labels.get((cid, name))
                if not labels or labels.get("untracked"):
                    continue
                remaining = self._quota_tracker.get_remaining(name, cid)
                acc = next((a for a in accounts if a["name"] == name), {})
                daily_limit = acc.get("daily_limit", 5)
                used = daily_limit - remaining
                labels["used"].configure(text=f"{used}/{daily_limit}",
                                         text_color="#ff5555" if remaining == 0 else "#ffffff")
                entry = self._quota_tracker._data.get(self._quota_tracker._key(name, cid))
                if entry:
                    secs = entry["window_start"] + 86400 - now
                    from core.quota import _format_duration
                    labels["resets"].configure(text=_format_duration(secs) if secs > 0 else "—")
                else:
                    labels["resets"].configure(text="—")

    def _build_quota_widgets(self, accounts, content_map, game_names) -> None:
        now = time.time()
        for cid, names in content_map.items():
            ctk.CTkLabel(self._quota_scroll, text=f"{game_names.get(cid, f'EA {cid}')} ({cid})",
                         font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=6, pady=(10, 4))
            table = ctk.CTkFrame(self._quota_scroll)
            table.pack(fill="x", padx=6, pady=(0, 4))
            for c, w in enumerate([3, 1, 1, 1]):
                table.grid_columnconfigure(c, weight=w)
            for col, text in enumerate(["Account", "Used", "Resets In", ""]):
                ctk.CTkLabel(table, text=text, font=("Segoe UI", 10, "bold"),
                             text_color="#aaaaaa").grid(row=0, column=col, sticky="w", padx=6, pady=2)

            for i, name in enumerate(names, start=1):
                acc = next((a for a in accounts if a["name"] == name), {})
                track = acc.get("track_quota", True)
                daily_limit = acc.get("daily_limit", 5)
                ctk.CTkLabel(table, text=name, anchor="w").grid(row=i, column=0, sticky="w", padx=6, pady=1)

                if not track:
                    lbl_used = ctk.CTkLabel(table, text="Untracked", anchor="w", text_color="#888888")
                    lbl_used.grid(row=i, column=1, sticky="w", padx=6, pady=1)
                    lbl_resets = ctk.CTkLabel(table, text="—", anchor="w")
                    lbl_resets.grid(row=i, column=2, sticky="w", padx=6, pady=1)
                    self._quota_labels[(cid, name)] = {"used": lbl_used, "resets": lbl_resets, "untracked": True}
                    continue

                remaining = self._quota_tracker.get_remaining(name, cid)
                used = daily_limit - remaining
                lbl_used = ctk.CTkLabel(table, text=f"{used}/{daily_limit}", anchor="w",
                                        text_color="#ff5555" if remaining == 0 else "#ffffff")
                lbl_used.grid(row=i, column=1, sticky="w", padx=6, pady=1)

                entry = self._quota_tracker._data.get(self._quota_tracker._key(name, cid))
                resets_text = "—"
                if entry:
                    secs = entry["window_start"] + 86400 - now
                    if secs > 0:
                        from core.quota import _format_duration
                        resets_text = _format_duration(secs)
                lbl_resets = ctk.CTkLabel(table, text=resets_text, anchor="w")
                lbl_resets.grid(row=i, column=2, sticky="w", padx=6, pady=1)
                self._quota_labels[(cid, name)] = {"used": lbl_used, "resets": lbl_resets, "untracked": False}

                bf = ctk.CTkFrame(table, fg_color="transparent")
                bf.grid(row=i, column=3, padx=4, pady=1)
                ctk.CTkButton(bf, text="-", width=28, height=24,
                             command=lambda n=name, c=cid: self._quota_decrement(n, c)).pack(side="left", padx=1)
                ctk.CTkButton(bf, text="+", width=28, height=24,
                             command=lambda n=name, c=cid: self._quota_increment(n, c)).pack(side="left", padx=1)

    def _quota_increment(self, name: str, content_id: str) -> None:
        if self._quota_tracker:
            self._quota_tracker.record(name, content_id)
            self._refresh_quota_panel()

    def _quota_decrement(self, name: str, content_id: str) -> None:
        if self._quota_tracker:
            self._quota_tracker.decrement(name, content_id)
            self._refresh_quota_panel()

    # --------------------------------------------------------- status/logs --
    def _set_status(self, status: str, label: str | None = None) -> None:
        color = STATUS_COLORS.get(status, "#888888")
        self._status_dot.configure(text_color=color)
        self._status_label.configure(text=(label or status.upper()), text_color=color)

    def update_queue_state(self, state: dict) -> None:
        with self._queue_state_lock:
            self._queue_state = state
        self.after(0, self._refresh_job_panel)

    def _refresh_job_panel(self) -> None:
        with self._queue_state_lock:
            state = self._queue_state
        current = state.get("current")
        pending = state.get("pending")
        if current:
            self._lbl_content_id.configure(text=current.get("content_id", "—"))
            self._lbl_account.configure(text=str(current.get("account_name", "—")))
            self._lbl_job_status.configure(text=current.get("status", "—").upper())
            self._set_status("busy", "PROCESSING")
        else:
            self._lbl_content_id.configure(text="—")
            self._lbl_account.configure(text="—")
            self._lbl_job_status.configure(text="—")
            self._set_status("idle" if self._server_running else "stopped")
        self._lbl_pending.configure(text=pending.get("content_id", "?") if pending else "empty")
        try:
            from core import account_manager
            self._active_info.configure(text=f"Active: {account_manager.get_active() or '—'}")
        except Exception:
            pass

    def get_log_handler(self) -> GuiLogHandler:
        return GuiLogHandler(self._log_queue)

    def _poll_logs(self) -> None:
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_logs)

    def _append_log(self, record: logging.LogRecord) -> None:
        line = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S").format(record) + "\n"
        self._log_box.configure(state="normal")
        self._log_box._textbox.insert("end", line, record.levelname)
        self._log_box._textbox.see("end")
        self._log_box.configure(state="disabled")

    def _clear_logs(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("0.0", "end")
        self._log_box.configure(state="disabled")

    def _poll_state(self) -> None:
        self._refresh_job_panel()
        self.after(500, self._poll_state)

    def _toggle_server(self) -> None:
        self._server_running = not self._server_running
        self._on_toggle_server(self._server_running)
        self._toggle_btn.configure(text="Stop Server" if self._server_running else "Start Server")
        self._set_status("idle" if self._server_running else "stopped")

    def set_server_running(self, running: bool) -> None:
        self._server_running = running
        self._toggle_btn.configure(text="Stop Server" if running else "Start Server")

    def _info_row(self, parent, label: str, value: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=1)
        ctk.CTkLabel(row, text=f"{label}:", width=80, anchor="w", text_color="#aaaaaa").pack(side="left")
        val = ctk.CTkLabel(row, text=value, anchor="w")
        val.pack(side="left")
        return val

    def _on_close(self) -> None:
        self.destroy()
