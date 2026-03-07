#!/usr/bin/env python3
"""
Display-Client für Hallenvisualisierung
Zeigt abwechselnd Spurbezeichnung und Kennzahlenbild auf einem HDMI-Bildschirm an.
Synchronisierung aller Displays über NTP (epoch_seconds % interval == 0).
"""

import json
import logging
import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional

import pygame
import requests

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
}

IMAGE_NAMES = ["spur_bezeichnung.png", "kennzahlen.png"]

# Farben
COLOR_BLACK = (0, 0, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_GRAY = (40, 40, 40)
COLOR_YELLOW = (255, 200, 0)
COLOR_RED = (200, 60, 60)

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
# Bild-Loader
# ---------------------------------------------------------------------------


def load_images(folder: str, screen_size: tuple) -> list[Optional[pygame.Surface]]:
    """Lädt die beiden PNG-Bilder aus dem Ordner und skaliert sie auf Bildschirmgröße."""
    surfaces = []
    for name in IMAGE_NAMES:
        path = Path(folder) / name
        if path.exists():
            try:
                img = pygame.image.load(str(path)).convert()
                img = pygame.transform.smoothscale(img, screen_size)
                surfaces.append(img)
                log.info(f"Bild geladen: {path}")
            except Exception as e:
                log.warning(f"Bild konnte nicht geladen werden ({path}): {e}")
                surfaces.append(None)
        else:
            log.warning(f"Bild nicht gefunden: {path}")
            surfaces.append(None)
    return surfaces


def render_placeholder(screen: pygame.Surface, text: str, font: pygame.font.Font) -> None:
    """Zeigt einen Platzhalter an, wenn ein Bild fehlt."""
    screen.fill(COLOR_GRAY)
    label = font.render(text, True, COLOR_YELLOW)
    rect = label.get_rect(center=(screen.get_width() // 2, screen.get_height() // 2))
    screen.blit(label, rect)


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
    }
    try:
        resp = requests.post(f"{url}/api/clients/register", json=payload, timeout=5)
        resp.raise_for_status()
        log.info(f"Beim Server registriert: {url}")
    except Exception as e:
        log.warning(f"Registrierung fehlgeschlagen: {e}")


def send_heartbeat(cfg: dict, current_image_index: int, paused: bool) -> None:
    url = cfg.get("server_url", "")
    if not url:
        return
    payload = {
        "spur_name": cfg["spur_name"],
        "ip": get_local_ip(),
        "status": "paused" if paused else "online",
        "current_image": IMAGE_NAMES[current_image_index],
        "interval_seconds": cfg["interval_seconds"],
        "image_folder": cfg["image_folder"],
    }
    try:
        requests.post(f"{url}/api/clients/heartbeat", json=payload, timeout=3)
    except Exception:
        pass


def fetch_commands(cfg: dict) -> Optional[dict]:
    """Holt ausstehende Steuerbefehle vom Server."""
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
        self._manual_index: Optional[int] = None
        self._images_dirty = True
        self._running = True

        # Pygame initialisieren
        os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
        pygame.init()
        pygame.mouse.set_visible(False)

        info = pygame.display.Info()
        self.screen_size = (info.current_w, info.current_h)
        self.screen = pygame.display.set_mode(self.screen_size, pygame.FULLSCREEN)
        pygame.display.set_caption("Hallenvisualisierung")

        self.font_large = pygame.font.SysFont("monospace", 48, bold=True)
        self.font_small = pygame.font.SysFont("monospace", 24)

        self.images: list[Optional[pygame.Surface]] = [None, None]
        self.current_index = 0
        self.clock = pygame.time.Clock()

        # Hintergrundthread für Server-Kommunikation
        self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self._server_thread.start()

    # ------------------------------------------------------------------
    # Thread-sichere Properties
    # ------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @paused.setter
    def paused(self, value: bool):
        with self._lock:
            self._paused = value

    @property
    def manual_index(self) -> Optional[int]:
        with self._lock:
            return self._manual_index

    @manual_index.setter
    def manual_index(self, value: Optional[int]):
        with self._lock:
            self._manual_index = value

    @property
    def images_dirty(self) -> bool:
        with self._lock:
            return self._images_dirty

    @images_dirty.setter
    def images_dirty(self, value: bool):
        with self._lock:
            self._images_dirty = value

    # ------------------------------------------------------------------
    # Server-Loop (läuft in einem separaten Thread)
    # ------------------------------------------------------------------

    def _reload_config(self):
        """Lädt config.json neu und übernimmt Änderungen."""
        new_cfg = load_config()
        with self._lock:
            old_folder = self.cfg.get("image_folder")
            old_url = self.cfg.get("server_url")
            old_spur = self.cfg.get("spur_name")
            self.cfg = new_cfg
            if new_cfg.get("image_folder") != old_folder:
                self._images_dirty = True
                log.info(f"Config: Bildordner geändert: {old_folder} -> {new_cfg.get('image_folder')}")
            server_changed = new_cfg.get("server_url") != old_url
            spur_changed = new_cfg.get("spur_name") != old_spur
        if server_changed or spur_changed:
            log.info(f"Config: Server-URL geändert: {old_url} -> {new_cfg.get('server_url')}")
            register_with_server(new_cfg)

    def _server_loop(self):
        """Registrierung, Heartbeat und Command-Polling im Hintergrund."""
        register_with_server(self.cfg)
        last_heartbeat = 0.0
        last_config_reload = 0.0

        while self._running:
            now = time.time()

            # Config alle 10 Sekunden neu laden
            if now - last_config_reload >= 10:
                self._reload_config()
                last_config_reload = now

            # Aktuelle Config-Referenz holen
            with self._lock:
                cfg = self.cfg

            # Heartbeat alle 10 Sekunden
            if now - last_heartbeat >= 10:
                send_heartbeat(cfg, self.current_index, self.paused)
                last_heartbeat = now

            # Steuerbefehle via HTTP-Polling abfragen
            commands = fetch_commands(cfg)
            if commands:
                self._apply_commands(commands)

            # Steuerbefehle aus lokaler Command-Datei lesen (von command_receiver.py)
            self._read_command_file()

            time.sleep(2)

    def _read_command_file(self):
        """Liest und verarbeitet Befehle aus der temporären Command-Datei."""
        cmd_file = Path("/tmp/display_command.json")
        if cmd_file.exists():
            try:
                data = json.loads(cmd_file.read_text())
                cmd_file.unlink()
                self._apply_commands(data)
            except Exception as e:
                log.warning(f"Fehler beim Lesen der Command-Datei: {e}")
                try:
                    cmd_file.unlink()
                except Exception:
                    pass

    def _apply_commands(self, commands: dict):
        """Verarbeitet Steuerbefehle vom Admin-Server."""
        cmd = commands.get("command")

        if cmd == "pause":
            self.paused = True
            log.info("Pause aktiviert")

        elif cmd == "resume":
            self.paused = False
            log.info("Pause deaktiviert")

        elif cmd == "next_image":
            self.manual_index = (self.current_index + 1) % len(IMAGE_NAMES)
            log.info("Manueller Bildwechsel")

        elif cmd == "set_image":
            idx = commands.get("image_index", 0)
            self.manual_index = idx % len(IMAGE_NAMES)
            log.info(f"Bild gesetzt auf Index {self.manual_index}")

        elif cmd == "set_interval":
            new_interval = int(commands.get("interval_seconds", self.cfg["interval_seconds"]))
            if new_interval >= 5:
                with self._lock:
                    self.cfg["interval_seconds"] = new_interval
                save_config(self.cfg)
                log.info(f"Intervall geändert auf {new_interval}s")

        elif cmd == "set_folder":
            new_folder = commands.get("image_folder", "")
            if new_folder:
                with self._lock:
                    self.cfg["image_folder"] = new_folder
                self.images_dirty = True
                save_config(self.cfg)
                log.info(f"Bildordner geändert auf {new_folder}")

        elif cmd == "reload_images":
            self.images_dirty = True
            log.info("Bilder werden neu geladen")

    # ------------------------------------------------------------------
    # Haupt-Loop
    # ------------------------------------------------------------------

    def _get_target_index(self) -> int:
        """Berechnet den aktuellen Bildindex basierend auf NTP-synchronisierter Zeit."""
        epoch = int(time.time())
        interval = self.cfg["interval_seconds"]
        slot = epoch // interval
        return slot % len(IMAGE_NAMES)

    def run(self):
        log.info(f"Display-Client gestartet: {self.cfg['spur_name']}")
        log.info(f"Bildordner: {self.cfg['image_folder']}")
        log.info(f"Intervall: {self.cfg['interval_seconds']}s")
        log.info(f"Server: {self.cfg['server_url']}")

        while self._running:
            # Events verarbeiten
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._quit()
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        self._quit()
                    elif event.key == pygame.K_SPACE:
                        self.paused = not self.paused
                    elif event.key == pygame.K_RIGHT:
                        self.manual_index = (self.current_index + 1) % len(IMAGE_NAMES)
                    elif event.key == pygame.K_r:
                        self.images_dirty = True

            # Bilder nachladen falls nötig
            if self.images_dirty:
                self.images = load_images(self.cfg["image_folder"], self.screen_size)
                self.images_dirty = False

            # Index bestimmen
            mi = self.manual_index
            if mi is not None:
                self.current_index = mi
                self.manual_index = None
            elif not self.paused:
                self.current_index = self._get_target_index()

            # Bild anzeigen
            surface = self.images[self.current_index]
            if surface is not None:
                self.screen.blit(surface, (0, 0))
            else:
                label = f"Bild fehlt: {IMAGE_NAMES[self.current_index]}"
                render_placeholder(self.screen, label, self.font_large)

            # Overlay
            self._draw_overlay()

            pygame.display.flip()
            self.clock.tick(10)

    def _draw_overlay(self):
        """Zeigt Spur-Name und aktuelle Zeit klein in der oberen rechten Ecke."""
        now_str = time.strftime("%H:%M:%S")
        interval = self.cfg["interval_seconds"]
        remaining = interval - (int(time.time()) % interval)
        status = "PAUSE" if self.paused else f"{now_str}  [{remaining}s]"
        text = f"{self.cfg['spur_name']}  {status}"
        label = self.font_small.render(text, True, COLOR_WHITE)
        bg = pygame.Surface((label.get_width() + 16, label.get_height() + 8))
        bg.set_alpha(150)
        bg.fill(COLOR_BLACK)
        x = self.screen_size[0] - label.get_width() - 24
        y = 8
        self.screen.blit(bg, (x - 8, y))
        self.screen.blit(label, (x, y + 4))

    def _quit(self):
        log.info("Display-Client wird beendet")
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
        pygame.quit()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        app = DisplayApp()
        app.run()
    except Exception as e:
        log.error(f"Kritischer Fehler: {e}", exc_info=True)
        pygame.quit()
        sys.exit(1)
