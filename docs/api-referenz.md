# API-Referenz – Hallenvisualisierung Admin-Server

Base-URL: `http://<server-ip>:8080`

---

## Client-Registrierung

### POST /api/clients/register

Registriert einen Display-Client.

```json
{
  "spur_name": "Spur-01",
  "ip": "192.168.1.10",
  "status": "online",
  "interval_seconds": 30,
  "image_folder": "/home/pi/spur-bilder"
}
```

### POST /api/clients/heartbeat

Periodischer Heartbeat (alle 10s vom Client gesendet).

```json
{
  "spur_name": "Spur-01",
  "ip": "192.168.1.10",
  "status": "online",
  "current_image": "spur_bezeichnung.png",
  "interval_seconds": 30,
  "image_folder": "/home/pi/spur-bilder"
}
```

### POST /api/clients/unregister

Meldet einen Client ab.

```json
{
  "spur_name": "Spur-01"
}
```

---

## Status-Abfragen

### GET /api/clients

Gibt alle registrierten Clients zurück.

**Response:**
```json
[
  {
    "spur_name": "Spur-01",
    "ip": "192.168.1.10",
    "status": "online",
    "paused": false,
    "interval_seconds": 30,
    "image_folder": "/home/pi/spur-bilder",
    "current_image": "spur_bezeichnung.png",
    "last_seen": 1709726400.0
  }
]
```

### GET /api/clients/{spur_name}

Gibt einen einzelnen Client zurück.

---

## Steuerbefehle

### POST /api/clients/{spur_name}/command

Sendet einen Befehl an einen spezifischen Client.

```json
{
  "command": "pause"
}
```

#### Verfuegbare Befehle

| command | Parameter | Beschreibung |
|---|---|---|
| `pause` | – | Bildwechsel anhalten |
| `resume` | – | Bildwechsel fortsetzen |
| `next_image` | – | Manuell zum naechsten Bild |
| `set_image` | `image_index: 0\|1` | Bestimmtes Bild anzeigen |
| `set_interval` | `interval_seconds: int` | Wechselintervall setzen (min. 5s) |
| `set_folder` | `image_folder: str` | Bildordner-Pfad aendern |
| `reload_images` | – | Bilder aus Ordner neu laden |

### POST /api/global/command

Sendet einen Befehl an ALLE Clients.

```json
{
  "command": "set_interval",
  "interval_seconds": 60
}
```

### GET /api/clients/{spur_name}/commands

Polling-Endpunkt: Client holt ausstehenden Befehl ab. Gibt `{}` zurueck wenn kein Befehl ansteht.

---

## Bilder-Upload

### POST /api/clients/{spur_name}/upload

Laedt ein Bild hoch und uebertraegt es an den Display-Client.

**Content-Type:** `multipart/form-data`

| Feld | Typ | Beschreibung |
|---|---|---|
| `image_type` | string | `spur_bezeichnung` oder `kennzahlen` |
| `file` | file | PNG- oder JPEG-Datei |

**Response:**
```json
{
  "ok": true,
  "delivered": true,
  "filename": "spur_bezeichnung.png"
}
```

---

## Display-Client HTTP-Server (Port 8081)

Laeuft auf jedem Display-Pi.

### POST /command

Empfaengt einen Steuerbefehl direkt.

```json
{
  "command": "pause"
}
```

### POST /upload

Empfaengt eine Bilddatei und speichert sie im Bildordner.

**Content-Type:** `multipart/form-data`

### GET /status

Gibt den aktuellen Status und die Konfiguration zurueck.
