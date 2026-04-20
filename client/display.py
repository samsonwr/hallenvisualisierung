#!/usr/bin/env python3
"""
Display-Client für Hallenvisualisierung
Zeigt abwechselnd konfigurierte Slots (Website oder Bild) im Vollbild an.
Synchronisierung aller Displays über NTP. Steuert Chromium via CDP.
"""

import asyncio
import json
import logging
import queue as stdlib_queue
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import requests
import websockets

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/display_client.log"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "spur_name": "Spur-01",
    "image_folder": "/home/pi/spur-bilder",
    "interval_seconds": 30,
    "server_url": "http://localhost:8080",
    "slots": ["spur_bezeichnung", "kennzahlen"],
    "website_url": "",
    "website_username": "",
    "website_password": "",
}

BROWSER_PROFILE_DIR = "/home/pi/.hallenvis-browser"
CHROMIUM_DEBUG_PORT = 9222
LOCAL_DISPLAY_PORT = 8081  # command_receiver.py


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            log.warning(f"Fehler beim Laden der config.json: {e} – verwende Defaults")
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        log.info("config.json gespeichert")
    except Exception as e:
        log.error(f"Fehler beim Speichern der config.json: {e}")


# ---------------------------------------------------------------------------
# Server-Kommunikation
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def register_with_server(cfg: dict) -> None:
    url = cfg.get("server_url", "")
    if not url:
        return
    payload = {
        "spur_name": cfg["spur_name"],
        "ip": get_local_ip(),
        "status": "online",
        "interval_seconds": cfg["interval_seconds"],
        "image_folder": cfg["image_folder"],
        "website_url": cfg.get("website_url", ""),
        "slots": cfg.get("slots", []),
    }
    try:
        resp = requests.post(f"{url}/api/clients/register", json=payload, timeout=5)
        resp.raise_for_status()
        log.info(f"Beim Server registriert: {url}")
    except Exception as e:
        log.warning(f"Registrierung fehlgeschlagen: {e}")


def send_heartbeat(cfg: dict, current_slot: str, paused: bool, login_required: bool) -> None:
    url = cfg.get("server_url", "")
    if not url:
        return
    if paused:
        status = "paused"
    elif login_required:
        status = "login_required"
    else:
        status = "online"
    payload = {
        "spur_name": cfg["spur_name"],
        "ip": get_local_ip(),
        "status": status,
        "current_image": current_slot,
        "interval_seconds": cfg["interval_seconds"],
        "image_folder": cfg["image_folder"],
        "website_url": cfg.get("website_url", ""),
        "slots": cfg.get("slots", []),
        "login_required": login_required,
    }
    try:
        requests.post(f"{url}/api/clients/heartbeat", json=payload, timeout=3)
    except Exception:
        pass


def fetch_commands(cfg: dict) -> Optional[dict]:
    url = cfg.get("server_url", "")
    if not url:
        return None
    try:
        resp = requests.get(
            f"{url}/api/clients/{cfg['spur_name']}/commands",
            timeout=3,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Haupt-Anwendungsklasse
# ---------------------------------------------------------------------------

class DisplayApp:
    def __init__(self):
        self.cfg = load_config()
        self._lock = threading.Lock()
        self._paused = False
        self._manual_slot: Optional[str] = None
        self._running = True
        self._current_slot: Optional[str] = None
        self._login_required = False
        self._force_navigate = False
        self._cmd_queue: stdlib_queue.Queue = stdlib_queue.Queue()

        # CDP
        self._chrome_proc: Optional[subprocess.Popen] = None
        self._chrome_ws = None
        self._msg_id = 0
        self._pending: dict = {}
        self._recv_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Chromium-Start
    # ------------------------------------------------------------------

    def _launch_chromium(self):
        args = [
            "--kiosk",
            "--noerrdialogs",
            "--disable-infobars",
            "--disable-translate",
            "--no-first-run",
            "--disable-features=TranslateUI",
            f"--remote-debugging-port={CHROMIUM_DEBUG_PORT}",
            f"--user-data-dir={BROWSER_PROFILE_DIR}",
            "--restore-last-session",
            "about:blank",
        ]
        for binary in ("chromium-browser", "chromium"):
            try:
                self._chrome_proc = subprocess.Popen([binary] + args)
                log.info(f"Chromium gestartet ({binary}, PID {self._chrome_proc.pid})")
                return
            except FileNotFoundError:
                continue
        log.error("Chromium nicht gefunden. Bitte installieren: sudo apt install chromium-browser")
        sys.exit(1)

    # ------------------------------------------------------------------
    # CDP-Verbindung
    # ------------------------------------------------------------------

    async def _connect_cdp(self) -> bool:
        for attempt in range(20):
            try:
                raw = urllib.request.urlopen(
                    f"http://localhost:{CHROMIUM_DEBUG_PORT}/json", timeout=2
                ).read().decode()
                tabs = json.loads(raw)
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                    self._chrome_ws = await websockets.connect(ws_url, max_size=None)
                    self._recv_task = asyncio.create_task(self._recv_loop())
                    log.info(f"CDP-Verbindung hergestellt: {ws_url}")
                    return True
            except Exception as e:
                log.debug(f"CDP-Verbindungsversuch {attempt + 1}/20: {e}")
                await asyncio.sleep(1)
        log.error("CDP-Verbindung fehlgeschlagen nach 20 Versuchen")
        return False

    async def _recv_loop(self):
        """Empfängt CDP-Nachrichten und leitet Antworten an wartende Futures weiter."""
        try:
            async for raw in self._chrome_ws:
                try:
                    msg = json.loads(raw)
                    mid = msg.get("id")
                    if mid is not None and mid in self._pending:
                        fut = self._pending.pop(mid)
                        if not fut.done():
                            fut.set_result(msg.get("result", {}))
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"CDP recv loop beendet: {e}")

    async def _send_cdp(self, method: str, params: dict = None) -> dict:
        if not self._chrome_ws:
            return {}
        loop = asyncio.get_event_loop()
        self._msg_id += 1
        mid = self._msg_id
        fut = loop.create_future()
        self._pending[mid] = fut
        payload = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        try:
            await self._chrome_ws.send(json.dumps(payload))
            return await asyncio.wait_for(asyncio.shield(fut), timeout=5)
        except Exception as e:
            self._pending.pop(mid, None)
            log.debug(f"CDP {method} Fehler: {e}")
            return {}

    async def _navigate(self, url: str):
        log.info(f"Navigiere zu: {url[:80]}{'...' if len(url) > 80 else ''}")
        await self._send_cdp("Page.navigate", {"url": url})

    async def _get_current_url(self) -> str:
        result = await self._send_cdp(
            "Runtime.evaluate",
            {"expression": "window.location.href", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    # ------------------------------------------------------------------
    # Slot-Logik
    # ------------------------------------------------------------------

    def _get_target_slot(self) -> str:
        slots = self.cfg.get("slots") or ["spur_bezeichnung", "kennzahlen"]
        epoch = int(time.time())
        interval = self.cfg["interval_seconds"]
        return slots[(epoch // interval) % len(slots)]

    def _slot_url(self, slot: str) -> str:
        if slot == "website":
            return self.cfg.get("website_url") or "about:blank"
        spur = urllib.parse.quote(self.cfg.get("spur_name", ""), safe="")
        interval = self.cfg.get("interval_seconds", 30)
        ts = int(time.time())
        return (
            f"http://localhost:{LOCAL_DISPLAY_PORT}/display/image/{slot}"
            f"?spur={spur}&interval={interval}&t={ts}"
        )

    # ------------------------------------------------------------------
    # Haupt-Loop
    # ------------------------------------------------------------------

    async def _main_loop(self):
        url_check_counter = 0

        while self._running:
            await self._process_commands()

            with self._lock:
                paused = self._paused
                manual = self._manual_slot
                force = self._force_navigate

            if manual:
                target_slot = manual
                with self._lock:
                    self._manual_slot = None
            elif paused:
                target_slot = self._current_slot or self._get_target_slot()
            else:
                target_slot = self._get_target_slot()

            if target_slot and (target_slot != self._current_slot or force):
                await self._navigate(self._slot_url(target_slot))
                self._current_slot = target_slot
                with self._lock:
                    self._force_navigate = False

            # Login-Status prüfen (alle ~10s)
            url_check_counter += 1
            if url_check_counter >= 10:
                url_check_counter = 0
                if self._current_slot == "website":
                    cur_url = await self._get_current_url()
                    login_kws = ("login", "signin", "authenticate", "logon", "sap-login")
                    self._login_required = bool(cur_url) and any(
                        k in cur_url.lower() for k in login_kws
                    )
                else:
                    self._login_required = False

            await asyncio.sleep(1)

    async def _process_commands(self):
        while not self._cmd_queue.empty():
            try:
                cmd = self._cmd_queue.get_nowait()
                await self._handle_command(cmd)
            except stdlib_queue.Empty:
                break

    async def _handle_command(self, commands: dict):
        cmd = commands.get("command")

        if cmd == "pause":
            with self._lock:
                self._paused = True
            log.info("Pause aktiviert")

        elif cmd == "resume":
            with self._lock:
                self._paused = False
            log.info("Pause deaktiviert")

        elif cmd == "next_image":
            slots = self.cfg.get("slots") or ["spur_bezeichnung", "kennzahlen"]
            cur = self._current_slot
            idx = (slots.index(cur) + 1) % len(slots) if cur in slots else 0
            with self._lock:
                self._manual_slot = slots[idx]
            log.info("Manueller Slot-Wechsel")

        elif cmd == "set_image":
            slots = self.cfg.get("slots") or ["spur_bezeichnung", "kennzahlen"]
            idx = commands.get("image_index", 0) % len(slots)
            with self._lock:
                self._manual_slot = slots[idx]
            log.info(f"Slot gesetzt auf Index {idx}")

        elif cmd == "set_interval":
            val = int(commands.get("interval_seconds", self.cfg["interval_seconds"]))
            if val >= 5:
                with self._lock:
                    self.cfg["interval_seconds"] = val
                save_config(self.cfg)
                log.info(f"Intervall: {val}s")

        elif cmd == "set_folder":
            folder = commands.get("image_folder", "")
            if folder:
                with self._lock:
                    self.cfg["image_folder"] = folder
                    self._force_navigate = True
                save_config(self.cfg)
                log.info(f"Bildordner: {folder}")

        elif cmd == "reload_images":
            if self._current_slot and self._current_slot != "website":
                with self._lock:
                    self._force_navigate = True
            log.info("Bilder werden neu geladen")

        elif cmd == "set_website_url":
            new_url = commands.get("website_url", "")
            if new_url:
                with self._lock:
                    self.cfg["website_url"] = new_url
                save_config(self.cfg)
                if self._current_slot == "website":
                    await self._navigate(new_url)
                log.info(f"Website-URL: {new_url}")

        elif cmd == "set_slots":
            new_slots = commands.get("slots", [])
            if new_slots and isinstance(new_slots, list):
                with self._lock:
                    self.cfg["slots"] = new_slots
                save_config(self.cfg)
                log.info(f"Slots: {new_slots}")

    # ------------------------------------------------------------------
    # Server-Loop (Thread)
    # ------------------------------------------------------------------

    def _server_loop(self):
        register_with_server(self.cfg)
        last_heartbeat = 0.0

        while self._running:
            now = time.time()
            with self._lock:
                cfg = self.cfg

            if now - last_heartbeat >= 10:
                send_heartbeat(cfg, self._current_slot or "", self._paused, self._login_required)
                last_heartbeat = now

            commands = fetch_commands(cfg)
            if commands:
                self._cmd_queue.put(commands)

            self._read_command_file()
            time.sleep(2)

    def _read_command_file(self):
        cmd_file = Path("/tmp/display_command.json")
        if cmd_file.exists():
            try:
                data = json.loads(cmd_file.read_text())
                cmd_file.unlink()
                self._cmd_queue.put(data)
            except Exception as e:
                log.warning(f"Fehler beim Lesen der Command-Datei: {e}")
                try:
                    cmd_file.unlink()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def run(self):
        log.info(f"Display-Client gestartet: {self.cfg['spur_name']}")
        log.info(f"Slots: {self.cfg.get('slots')}")
        log.info(f"Intervall: {self.cfg['interval_seconds']}s")
        log.info(f"Server: {self.cfg['server_url']}")

        self._launch_chromium()
        await asyncio.sleep(3)

        if not await self._connect_cdp():
            log.error("Kein CDP verfügbar – Beende")
            return

        self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self._server_thread.start()

        try:
            await self._main_loop()
        finally:
            self._running = False
            url = self.cfg.get("server_url", "")
            if url:
                try:
                    requests.post(
                        f"{url}/api/clients/unregister",
                        json={"spur_name": self.cfg["spur_name"]},
                        timeout=3,
                    )
                except Exception:
                    pass
            if self._chrome_proc:
                self._chrome_proc.terminate()


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        app = DisplayApp()
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error(f"Kritischer Fehler: {e}", exc_info=True)
        sys.exit(1)
