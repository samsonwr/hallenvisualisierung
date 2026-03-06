#!/usr/bin/env python3
"""
Kleiner HTTP-Server, der auf jedem Display-Pi läuft und direkte Steuerbefehle
vom Admin-Server sowie Bilder-Uploads entgegennimmt.
Kommuniziert mit display.py über eine gemeinsame Queue-Datei.
"""

import json
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import uvicorn

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COMMAND_FILE = Path("/tmp/display_command.json")
CONFIG_PATH = Path(__file__).parent / "config.json"

app = FastAPI(title="Display-Client Receiver")


class Command(BaseModel):
    command: str
    interval_seconds: int | None = None
    image_folder: str | None = None
    image_index: int | None = None


def write_command(cmd: dict):
    """Schreibt Befehl in die Command-Datei, die display.py pollt."""
    COMMAND_FILE.write_text(json.dumps(cmd))


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


@app.post("/command")
async def receive_command(cmd: Command):
    data = cmd.model_dump(exclude_none=True)
    write_command(data)

    # Bei Konfigurationsändernden Befehlen config.json sofort aktualisieren
    cfg = load_config()
    if cmd.command == "set_interval" and cmd.interval_seconds:
        cfg["interval_seconds"] = cmd.interval_seconds
        save_config(cfg)
    elif cmd.command == "set_folder" and cmd.image_folder:
        cfg["image_folder"] = cmd.image_folder
        save_config(cfg)

    log.info(f"Befehl empfangen: {data}")
    return {"ok": True}


@app.post("/upload")
async def receive_image(file: UploadFile = File(...)):
    """Empfängt ein Bild und speichert es im konfigurierten Bildordner."""
    cfg = load_config()
    folder = cfg.get("image_folder", "/home/pi/spur-bilder")
    Path(folder).mkdir(parents=True, exist_ok=True)

    dest = Path(folder) / file.filename
    data = await file.read()
    dest.write_bytes(data)
    log.info(f"Bild gespeichert: {dest} ({len(data)} Bytes)")

    # Bilder-Reload auslösen
    write_command({"command": "reload_images"})
    return {"ok": True, "path": str(dest)}


@app.get("/status")
async def status():
    cfg = load_config()
    return {"ok": True, "spur_name": cfg.get("spur_name", "?"), "config": cfg}


if __name__ == "__main__":
    uvicorn.run("command_receiver:app", host="0.0.0.0", port=8081, log_level="info")
