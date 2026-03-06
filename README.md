# Hallenvisualisierung

Raspberry Pi 5 Anwendung zur Visualisierung von Montagespuren in einer Fertigungshalle.

Jeder Display-Pi zeigt im Vollbild abwechselnd zwei Bilder:
1. **Spurbezeichnung** (`spur_bezeichnung.png`)
2. **Kennzahlenbild** (`kennzahlen.png`)

Alle Displays wechseln **synchron** via NTP-Synchronisation.

---

## Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                     Admin-Server (PC/Pi)                    │
│  FastAPI :8080 – Web-UI + REST-API                          │
│  http://<server-ip>:8080  →  Dashboard im Browser           │
└────────────────────────┬────────────────────────────────────┘
                         │ REST (HTTP)
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
   │  Display-Pi │ │  Display-Pi │ │  Display-Pi │
   │  Spur-01    │ │  Spur-02    │ │  Spur-n     │
   │  :8081      │ │  :8081      │ │  :8081      │
   │  display.py │ │  display.py │ │  display.py │
   └─────────────┘ └─────────────┘ └─────────────┘
```

**NTP-Synchronisation:** Alle Pis wechseln das Bild wenn `int(time.time()) % interval == 0`.
Voraussetzung: alle Pis sind mit einem NTP-Server synchronisiert.

---

## Projektstruktur

```
hallenvisualisierung/
├── client/
│   ├── display.py              # Hauptprogramm: Pygame-Vollbildanzeige
│   ├── command_receiver.py     # HTTP-Server für direkte Steuerbefehle (:8081)
│   ├── config.json             # Konfiguration pro Pi
│   └── requirements.txt
├── server/
│   ├── main.py                 # FastAPI Admin-Server (:8080)
│   ├── static/
│   │   └── index.html          # Web-UI Dashboard
│   └── requirements.txt
├── config/
│   ├── display.service         # systemd: Display-Client
│   ├── display-receiver.service # systemd: Command-Receiver
│   └── admin-server.service    # systemd: Admin-Server
├── docs/
│   ├── architektur.md          # Architektur-Dokumentation
│   └── api-referenz.md         # REST-API Referenz
├── setup.sh                    # Automatisches Setup-Script
└── README.md
```

---

## Schnellstart

### Voraussetzungen

- Raspberry Pi OS **Bookworm** (64-bit), Python 3.11+
- Alle Pis im selben Netzwerk
- NTP aktiv auf allen Pis
- HDMI-Bildschirm am Display-Pi angeschlossen

---

### 1. Admin-Server einrichten

Auf dem Server-Rechner (separater Pi oder PC):

```bash
git clone <repo-url> hallenvisualisierung
cd hallenvisualisierung
sudo bash setup.sh --mode server
```

Web-UI aufrufen: `http://<server-ip>:8080`

---

### 2. Display-Client einrichten (pro Pi)

```bash
git clone <repo-url> hallenvisualisierung
cd hallenvisualisierung
sudo bash setup.sh \
  --mode client \
  --spur-name "Spur-01" \
  --server-url "http://192.168.1.100:8080" \
  --image-folder "/home/pi/spur-bilder" \
  --interval 30
```

**Argumente:**

| Argument | Beschreibung | Beispiel |
|---|---|---|
| `--mode` | `client` oder `server` | `client` |
| `--spur-name` | Eindeutiger Name der Spur | `Spur-01` |
| `--server-url` | URL des Admin-Servers | `http://192.168.1.100:8080` |
| `--image-folder` | Pfad zum Bildordner auf dem Pi | `/home/pi/spur-bilder` |
| `--interval` | Wechselintervall in Sekunden | `30` |

---

### 3. Bilder ablegen

Auf jedem Display-Pi zwei PNG-Bilder in den konfigurierten Ordner legen:

```
/home/pi/spur-bilder/
├── spur_bezeichnung.png   # Bild 1: Name/Bezeichnung der Spur
└── kennzahlen.png         # Bild 2: KPIs, Kennzahlen
```

**Empfohlene Bildgröße:** 1920×1080 px (Full HD) oder die native Auflösung des TVs.

Alternativ: Bilder über das **Web-UI** hochladen (werden automatisch an den Pi übertragen).

---

## Konfiguration (`client/config.json`)

```json
{
  "spur_name": "Spur-01",
  "image_folder": "/home/pi/spur-bilder",
  "interval_seconds": 30,
  "server_url": "http://192.168.1.100:8080"
}
```

| Feld | Beschreibung |
|---|---|
| `spur_name` | Eindeutiger Name dieser Spur (muss pro Pi unterschiedlich sein) |
| `image_folder` | Ordner mit den zwei PNG-Dateien |
| `interval_seconds` | Wechselintervall in Sekunden (muss durch den Wert teilbar sein) |
| `server_url` | Adresse des Admin-Servers |

---

## Admin-Server Web-UI

### Dashboard-Funktionen

- **Übersicht** aller registrierten Pis: Status (online/offline/pause), aktuelles Bild, IP, Intervall
- **Pro Spur:**
  - Bildordner-Pfad ändern
  - Intervall ändern
  - Pause / Fortsetzen
  - Manuell zu Bild 1 oder Bild 2 wechseln
  - Bilder hochladen (werden direkt an den Pi übertragen)
- **Global:**
  - Alle gleichzeitig pausieren / fortsetzen
  - Intervall für alle Pis gleichzeitig ändern

### REST-API Referenz

| Methode | Endpunkt | Beschreibung |
|---|---|---|
| `GET` | `/api/clients` | Alle registrierten Clients |
| `GET` | `/api/clients/{spur_name}` | Einzelner Client |
| `POST` | `/api/clients/register` | Client registrieren |
| `POST` | `/api/clients/heartbeat` | Heartbeat senden |
| `POST` | `/api/clients/{spur_name}/command` | Befehl senden |
| `POST` | `/api/global/command` | Befehl an alle senden |
| `POST` | `/api/clients/{spur_name}/upload` | Bild hochladen |

#### Steuerbefehle (`command`-Feld)

| Befehl | Parameter | Beschreibung |
|---|---|---|
| `pause` | – | Bildwechsel anhalten |
| `resume` | – | Bildwechsel fortsetzen |
| `next_image` | – | Manuell zum nächsten Bild wechseln |
| `set_image` | `image_index: 0\|1` | Direkt Bild 1 oder 2 anzeigen |
| `set_interval` | `interval_seconds: int` | Wechselintervall setzen |
| `set_folder` | `image_folder: str` | Bildordner-Pfad ändern |
| `reload_images` | – | Bilder aus Ordner neu laden |

---

## Systemd-Services

### Display-Pi

```bash
# Status prüfen
sudo systemctl status hallenvis-display
sudo systemctl status hallenvis-receiver

# Logs ansehen
sudo journalctl -u hallenvis-display -f
sudo journalctl -u hallenvis-receiver -f

# Neu starten
sudo systemctl restart hallenvis-display
sudo systemctl restart hallenvis-receiver
```

### Admin-Server

```bash
sudo systemctl status hallenvis-server
sudo journalctl -u hallenvis-server -f
sudo systemctl restart hallenvis-server
```

---

## Tastenkürzel (Display-Client)

Wenn eine Tastatur angeschlossen ist:

| Taste | Aktion |
|---|---|
| `ESC` / `Q` | Beenden |
| `Leertaste` | Pause / Fortsetzen |
| `→` | Nächstes Bild |
| `R` | Bilder neu laden |

---

## NTP-Synchronisation

**Wichtig:** Für synchronen Bildwechsel müssen alle Pis NTP-synchronisiert sein.

Status prüfen:
```bash
timedatectl status
# oder
ntpq -p
```

Falls NTP nicht aktiv:
```bash
sudo apt install ntp
sudo systemctl enable --now ntp
```

Der Algorithmus: `int(time.time()) // interval_seconds % 2` bestimmt,
welches Bild gezeigt wird. Da alle Pis dieselbe UTC-Zeit verwenden, wechseln
alle gleichzeitig.

---

## Weitere Dokumentation

- [Architektur](docs/architektur.md) – Komponentenübersicht und Kommunikationsflüsse
- [API-Referenz](docs/api-referenz.md) – Vollständige REST-API Dokumentation

---

## Bekannte Einschränkungen

- Die Bilder-Upload-Funktion überträgt Bilder direkt per HTTP an Port 8081 des Pis.
  Ist der Pi offline, wird das Bild auf dem Server gespeichert und beim nächsten
  erfolgreichen Poll übertragen.
- Der Bildwechsel ist auf ±1 Sekunde genau (abhängig von NTP-Drift).
- `command_receiver.py` muss auf jedem Display-Pi laufen (Port 8081),
  damit Direktbefehle sofort ankommen. Alternativ werden Befehle beim nächsten
  Polling-Zyklus (alle 2 Sekunden) abgeholt.

---

## Entwicklung

Lokales Testen ohne Pygame (z.B. auf dem Entwicklungsrechner):

```bash
# Admin-Server lokal starten
cd server
pip install -r requirements.txt
python main.py
# → http://localhost:8080

# Display-Client im Fenstermodus (Pygame ohne Vollbild):
# In display.py: pygame.FULLSCREEN durch 0 ersetzen
cd client
pip install -r requirements.txt
python display.py
```
