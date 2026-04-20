#!/usr/bin/env python3
"""
Kleiner HTTP-Server, der auf jedem Display-Pi läuft und direkte Steuerbefehle
vom Admin-Server sowie Bilder-Uploads entgegennimmt.
Kommuniziert mit display.py über eine gemeinsame Command-Datei (/tmp/display_command.json).
Stellt zusätzlich Endpunkte zur Bildanzeige für Chromium bereit.
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from pydantic import BaseModel
import uvicorn

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COMMAND_FILE = Path("/tmp/display_command.json")
CONFIG_PATH = Path(__file__).parent / "config.json"

app = FastAPI(title="Display-Client Receiver")

# ---------------------------------------------------------------------------
# HTML-Template für Vollbild-Bildanzeige
# ---------------------------------------------------------------------------
_IMAGE_HTML = """\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:100%;height:100%;background:#000;overflow:hidden}}
.container{{width:100vw;height:100vh;display:flex;align-items:center;justify-content:center}}
img{{max-width:100%;max-height:100%;object-fit:contain;display:block}}
#overlay{{
  position:fixed;top:8px;right:8px;
  background:rgba(0,0,0,0.6);color:#fff;
  font-family:monospace;font-size:14px;
  padding:4px 12px;border-radius:4px;z-index:9999;
}}
#placeholder{{color:#ffcc00;font-family:monospace;font-size:2rem;text-align:center;padding:2rem}}
</style>
</head>
<body>
<div class="container">{img_tag}</div>
<div id="overlay"></div>
<script>
const spurName={spur_name_json};
const interval={interval};
function upd(){{
  const now=Math.floor(Date.now()/1000);
  const rem=interval-(now%interval);
  const t=new Date().toLocaleTimeString('de-DE');
  document.getElementById('overlay').textContent=spurName+'  '+t+'  ['+rem+'s]';
}}
upd();setInterval(upd,1000);
</script>
</body>
</html>"""


class Command(BaseModel):
    command: str
    interval_seconds: int | None = None
    image_folder: str | None = None
    image_index: int | None = None
    website_url: str | None = None
    slots: list[str] | None = None


def write_command(cmd: dict):
    """Schreibt Befehl in die Command-Datei, die display.py pollt."""
    COMMAND_FILE.write_text(json.dumps(cmd))


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def cec_send(cec_cmd: str) -> bool:
    """Sendet einen CEC-Befehl über cec-client an den TV."""
    try:
        result = subprocess.run(
            ["cec-client", "-s", "-d", "1"],
            input=cec_cmd + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        log.info(f"CEC-Befehl '{cec_cmd}' gesendet (returncode={result.returncode})")
        return result.returncode == 0
    except FileNotFoundError:
        log.error("cec-client nicht gefunden – bitte 'sudo apt install cec-utils' ausführen")
        return False
    except subprocess.TimeoutExpired:
        log.warning(f"CEC-Befehl '{cec_cmd}' Timeout")
        return False
    except Exception as e:
        log.error(f"CEC-Fehler: {e}")
        return False


# ---------------------------------------------------------------------------
# Befehls-Endpunkt
# ---------------------------------------------------------------------------

@app.post("/command")
async def receive_command(cmd: Command):
    data = cmd.model_dump(exclude_none=True)

    # CEC-Befehle direkt ausführen (nicht an display.py weiterleiten)
    if cmd.command == "tv_on":
        success = cec_send("on 0")
        return {"ok": success}
    elif cmd.command == "tv_off":
        success = cec_send("standby 0")
        return {"ok": success}

    write_command(data)

    # Konfigurationsändernde Befehle sofort in config.json speichern
    cfg = load_config()
    if cmd.command == "set_interval" and cmd.interval_seconds:
        cfg["interval_seconds"] = cmd.interval_seconds
        save_config(cfg)
    elif cmd.command == "set_folder" and cmd.image_folder:
        cfg["image_folder"] = cmd.image_folder
        save_config(cfg)
    elif cmd.command == "set_website_url" and cmd.website_url:
        cfg["website_url"] = cmd.website_url
        save_config(cfg)
    elif cmd.command == "set_slots" and cmd.slots:
        cfg["slots"] = cmd.slots
        save_config(cfg)

    log.info(f"Befehl empfangen: {data}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bilder-Upload
# ---------------------------------------------------------------------------

@app.post("/upload")
async def receive_image(file: UploadFile = File(...)):
    """Empfängt ein Bild und speichert es im konfigurierten Bildordner."""
    cfg = load_config()
    folder = cfg.get("image_folder", "/home/pi/spur-bilder")
    Path(folder).mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Dateiname fehlt")

    allowed = {"spur_bezeichnung.png", "kennzahlen.png"}
    filename = file.filename
    if filename not in allowed:
        log.warning(f"Unerwarteter Dateiname: {filename} – wird trotzdem gespeichert")

    dest = Path(folder) / filename
    data = await file.read()
    dest.write_bytes(data)
    log.info(f"Bild gespeichert: {dest} ({len(data)} Bytes)")

    write_command({"command": "reload_images"})
    return {"ok": True, "path": str(dest)}


# ---------------------------------------------------------------------------
# Bildanzeige für Chromium
# ---------------------------------------------------------------------------

@app.get("/display/image/{name}")
async def display_image_page(name: str, spur: str = "", interval: int = 30):
    """Serviert eine Vollbild-HTML-Seite mit dem angeforderten Bild für Chromium."""
    cfg = load_config()
    folder = Path(cfg.get("image_folder", "/home/pi/spur-bilder"))
    img_path = folder / f"{name}.png"
    ts = int(time.time())

    if img_path.exists():
        img_tag = f'<img src="/img/{name}.png?t={ts}" alt="{name}" />'
    else:
        img_tag = f'<div id="placeholder">Bild nicht gefunden:<br>{name}.png<br><small>{folder}</small></div>'

    html = _IMAGE_HTML.format(
        img_tag=img_tag,
        spur_name_json=json.dumps(spur),
        interval=interval,
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/img/{name}.png")
async def serve_image_file(name: str):
    """Serviert die PNG-Bilddatei direkt (mit Cache-Busting über ?t= Parameter)."""
    cfg = load_config()
    folder = Path(cfg.get("image_folder", "/home/pi/spur-bilder"))
    img_path = folder / f"{name}.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Bild nicht gefunden: {name}.png")
    return FileResponse(
        str(img_path),
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/status")
async def status():
    cfg = load_config()
    return {"ok": True, "spur_name": cfg.get("spur_name", "?"), "config": cfg}


if __name__ == "__main__":
    uvicorn.run("command_receiver:app", host="0.0.0.0", port=8081, log_level="info")
