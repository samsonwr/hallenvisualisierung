#!/usr/bin/env python3
"""
Admin-Server für Hallenvisualisierung
Verwaltet alle registrierten Display-Clients und stellt ein Web-UI bereit.
"""

import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App-Konfiguration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Hallenvisualisierung Admin-Server", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# In-Memory Datenbank der registrierten Clients
# ---------------------------------------------------------------------------
clients: dict[str, dict] = {}

# Ausstehende Steuerbefehle: { spur_name: command_dict }
pending_commands: dict[str, dict] = {}

# Timeout in Sekunden, nach dem ein Client als offline gilt
CLIENT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Pydantic-Modelle
# ---------------------------------------------------------------------------

class ClientRegister(BaseModel):
    spur_name: str
    ip: str
    status: str = "online"
    interval_seconds: int = 30
    image_folder: str = ""
    website_url: str = ""
    slots: list[str] = []


class ClientHeartbeat(BaseModel):
    spur_name: str
    ip: str
    status: str = "online"
    current_image: str = ""
    interval_seconds: int = 30
    image_folder: str = ""
    website_url: str = ""
    slots: list[str] = []
    login_required: bool = False


class ClientUnregister(BaseModel):
    spur_name: str


class CommandPayload(BaseModel):
    command: str
    interval_seconds: Optional[int] = None
    image_folder: Optional[str] = None
    image_index: Optional[int] = None
    website_url: Optional[str] = None
    slots: Optional[list[str]] = None


class GlobalCommand(BaseModel):
    command: str
    interval_seconds: Optional[int] = None
    website_url: Optional[str] = None
    slots: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def is_online(client: dict) -> bool:
    return (time.time() - client.get("last_seen", 0)) < CLIENT_TIMEOUT


def client_status(client: dict) -> str:
    if not is_online(client):
        return "offline"
    if client.get("paused", False):
        return "paused"
    return "online"


async def push_command_to_client(client: dict, command: dict) -> bool:
    """Sendet einen Befehl direkt per HTTP an den Display-Client."""
    ip = client.get("ip")
    if not ip:
        return False
    url = f"http://{ip}:8081/command"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(url, json=command)
            return resp.status_code == 200
    except Exception as e:
        log.warning(f"Direktverbindung zu {ip} fehlgeschlagen: {e} – Befehl wird gepuffert")
        return False


async def push_image_to_client(client: dict, filename: str, data: bytes) -> bool:
    """Überträgt eine Bilddatei per HTTP an den Display-Client."""
    ip = client.get("ip")
    if not ip:
        return False
    url = f"http://{ip}:8081/upload"
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                url,
                files={"file": (filename, io.BytesIO(data), "image/png")},
            )
            return resp.status_code == 200
    except Exception as e:
        log.warning(f"Bildübertragung zu {ip} fehlgeschlagen: {e}")
        return False


# ---------------------------------------------------------------------------
# API-Endpunkte: Client-Registrierung & Heartbeat
# ---------------------------------------------------------------------------

@app.post("/api/clients/register")
async def register_client(data: ClientRegister):
    clients[data.spur_name] = {
        "spur_name": data.spur_name,
        "ip": data.ip,
        "status": "online",
        "paused": False,
        "interval_seconds": data.interval_seconds,
        "image_folder": data.image_folder,
        "website_url": data.website_url,
        "slots": data.slots,
        "current_image": "",
        "login_required": False,
        "last_seen": time.time(),
        "registered_at": time.time(),
    }
    log.info(f"Client registriert: {data.spur_name} @ {data.ip}")
    return {"ok": True}


@app.post("/api/clients/heartbeat")
async def heartbeat(data: ClientHeartbeat):
    is_paused = data.status == "paused"
    if data.spur_name in clients:
        clients[data.spur_name].update({
            "ip": data.ip,
            "status": data.status,
            "paused": is_paused,
            "interval_seconds": data.interval_seconds,
            "image_folder": data.image_folder,
            "website_url": data.website_url,
            "slots": data.slots,
            "current_image": data.current_image,
            "login_required": data.login_required,
            "last_seen": time.time(),
        })
    else:
        clients[data.spur_name] = {
            "spur_name": data.spur_name,
            "ip": data.ip,
            "status": data.status,
            "paused": is_paused,
            "interval_seconds": data.interval_seconds,
            "image_folder": data.image_folder,
            "website_url": data.website_url,
            "slots": data.slots,
            "current_image": data.current_image,
            "login_required": data.login_required,
            "last_seen": time.time(),
            "registered_at": time.time(),
        }
    return {"ok": True}


@app.post("/api/clients/unregister")
async def unregister_client(data: ClientUnregister):
    if data.spur_name in clients:
        clients[data.spur_name]["status"] = "offline"
        clients[data.spur_name]["last_seen"] = 0
        log.info(f"Client abgemeldet: {data.spur_name}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API-Endpunkte: Steuerbefehle
# ---------------------------------------------------------------------------

@app.get("/api/clients/{spur_name}/commands")
async def get_commands(spur_name: str):
    """Polling-Endpunkt: Client holt ausstehenden Befehl ab."""
    cmd = pending_commands.pop(spur_name, None)
    if cmd:
        return cmd
    return {}


@app.post("/api/clients/{spur_name}/command")
async def send_command(spur_name: str, payload: CommandPayload):
    """Sendet einen Steuerbefehl an einen spezifischen Client."""
    if spur_name not in clients:
        raise HTTPException(status_code=404, detail=f"Client '{spur_name}' nicht gefunden")

    client = clients[spur_name]
    command = payload.model_dump(exclude_none=True)

    sent = await push_command_to_client(client, command)
    if not sent:
        pending_commands[spur_name] = command

    if payload.command == "set_interval" and payload.interval_seconds:
        client["interval_seconds"] = payload.interval_seconds
    elif payload.command == "set_folder" and payload.image_folder:
        client["image_folder"] = payload.image_folder
    elif payload.command == "set_website_url" and payload.website_url:
        client["website_url"] = payload.website_url
    elif payload.command == "set_slots" and payload.slots:
        client["slots"] = payload.slots
    elif payload.command == "pause":
        client["paused"] = True
    elif payload.command == "resume":
        client["paused"] = False

    return {"ok": True, "direct_delivery": sent}


@app.post("/api/global/command")
async def global_command(payload: GlobalCommand):
    """Sendet einen Befehl an ALLE registrierten Clients."""
    results = {}
    for spur_name, client in clients.items():
        command = payload.model_dump(exclude_none=True)
        sent = await push_command_to_client(client, command)
        if not sent:
            pending_commands[spur_name] = command
        results[spur_name] = {"direct_delivery": sent}

        if payload.command == "set_interval" and payload.interval_seconds:
            client["interval_seconds"] = payload.interval_seconds
        elif payload.command == "pause":
            client["paused"] = True
        elif payload.command == "resume":
            client["paused"] = False

    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# API-Endpunkte: Bilder-Upload
# ---------------------------------------------------------------------------

@app.post("/api/clients/{spur_name}/upload")
async def upload_image(
    spur_name: str,
    image_type: str = Form(...),
    file: UploadFile = File(...),
):
    """Lädt ein Bild hoch und überträgt es an den jeweiligen Display-Client."""
    if spur_name not in clients:
        raise HTTPException(status_code=404, detail=f"Client '{spur_name}' nicht gefunden")

    if image_type not in ("spur_bezeichnung", "kennzahlen"):
        raise HTTPException(status_code=400, detail="image_type muss 'spur_bezeichnung' oder 'kennzahlen' sein")

    filename = f"{image_type}.png"
    data = await file.read()

    # Lokal zwischenspeichern
    upload_dir = BASE_DIR / "uploads" / spur_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / filename).write_bytes(data)
    log.info(f"Bild gespeichert: {upload_dir / filename} ({len(data)} Bytes)")

    client = clients[spur_name]
    success = await push_image_to_client(client, filename, data)

    if success:
        reload_cmd = {"command": "reload_images"}
        sent = await push_command_to_client(client, reload_cmd)
        if not sent:
            pending_commands[spur_name] = reload_cmd

    return {"ok": True, "delivered": success, "filename": filename}


# ---------------------------------------------------------------------------
# API-Endpunkte: Status-Abfragen
# ---------------------------------------------------------------------------

@app.get("/api/clients")
async def list_clients():
    """Gibt alle registrierten Clients mit aktuellem Status zurück."""
    result = []
    for c in clients.values():
        result.append({
            **c,
            "status": client_status(c),
        })
    return result


@app.get("/api/clients/{spur_name}")
async def get_client(spur_name: str):
    if spur_name not in clients:
        raise HTTPException(status_code=404, detail="Client nicht gefunden")
    c = clients[spur_name]
    return {**c, "status": client_status(c)}


# ---------------------------------------------------------------------------
# Web-UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html nicht gefunden</h1>", status_code=500)


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )
