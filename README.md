# Hallenvisualisierung

Raspberry Pi 5 Anwendung zur Visualisierung von Montagespuren in einer Fertigungshalle.

Jeder Display-Pi zeigt im Vollbild abwechselnd konfigurierbare **Slots** an:
- **Website** (z.B. SAP Analytics Cloud Dashboard)
- **Spurbezeichnung** (`spur_bezeichnung.png`)
- **Kennzahlenbild** (`kennzahlen.png`)

Welche Slots in welcher Reihenfolge gezeigt werden, ist pro Pi frei konfigurierbar.
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
   │  Chromium   │ │  Chromium   │ │  Chromium   │
   └─────────────┘ └─────────────┘ └─────────────┘
```

**Display-Technologie:** Chromium läuft im Kiosk-Modus. `display.py` steuert die Navigation
über das Chrome DevTools Protocol (CDP). Bilder werden über einen lokalen HTTP-Endpunkt
(`:8081/display/image/...`) als Vollbild-HTML serviert.

**NTP-Synchronisation:** Alle Pis wechseln den Slot wenn `(int(time.time()) // interval) % len(slots) == slot_index`.
Voraussetzung: alle Pis sind mit einem NTP-Server synchronisiert.

---

## Projektstruktur

```
hallenvisualisierung/
├── client/
│   ├── display.py              # Hauptprogramm: Chromium-Steuerung via CDP
│   ├── command_receiver.py     # HTTP-Server: Steuerbefehle + Bildanzeige (:8081)
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

### 3. Bilder ablegen (optional)

Auf jedem Display-Pi PNG-Bilder in den konfigurierten Ordner legen:

```
/home/pi/spur-bilder/
├── spur_bezeichnung.png   # Bild: Name/Bezeichnung der Spur
└── kennzahlen.png         # Bild: KPIs, Kennzahlen
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
  "server_url": "http://192.168.1.100:8080",
  "slots": ["website", "kennzahlen"],
  "website_url": "https://example.com/dashboard",
  "website_username": "",
  "website_password": ""
}
```

| Feld | Beschreibung |
|---|---|
| `spur_name` | Eindeutiger Name dieser Spur (muss pro Pi unterschiedlich sein) |
| `image_folder` | Ordner mit den PNG-Dateien |
| `interval_seconds` | Wechselintervall in Sekunden pro Slot |
| `server_url` | Adresse des Admin-Servers |
| `slots` | Reihenfolge der Slots: `website`, `spur_bezeichnung`, `kennzahlen` |
| `website_url` | URL der anzuzeigenden Website (pro Pi unterschiedlich) |
| `website_username` | Benutzername für Login (optional, für Referenz) |
| `website_password` | Passwort für Login (optional, für Referenz) |

### Slot-Konfiguration

Der `slots`-Array bestimmt, was in welcher Reihenfolge angezeigt wird:

```json
// Nur Bilder (Original-Modus):
"slots": ["spur_bezeichnung", "kennzahlen"]

// Website + ein Bild:
"slots": ["website", "kennzahlen"]

// Nur Website:
"slots": ["website"]

// Website + beide Bilder (3 Slots):
"slots": ["website", "spur_bezeichnung", "kennzahlen"]
```

Das Intervall gilt **pro Slot**. Bei 3 Slots und 30s Intervall wechselt jeder Slot alle 30 Sekunden.

---

## Website-Login (z.B. SAP Analytics Cloud)

Websites wie SAP Analytics Cloud erfordern eine einmalige Anmeldung.

### Erstanmeldung

1. Pi startet, Chromium öffnet sich im Kiosk-Modus
2. Falls keine gespeicherte Session vorhanden: Login-Seite der Website erscheint
3. Einmalig auf dem Display-Bildschirm einloggen (oder per VNC/Remote Desktop)
4. Session wird im Chromium-Profil unter `/home/pi/.hallenvis-browser` gespeichert
5. Nach Neustart: Session wird automatisch wiederhergestellt (`--restore-last-session`)

### Session abgelaufen

Das Admin-Dashboard zeigt einen lila Badge **"Login erforderlich"**, wenn `display.py` erkennt,
dass Chromium auf einer Login-Seite gelandet ist. Dann einmalig neu einloggen.

**Hinweis:** SAP Analytics Cloud Sessions können im SAP-System auf bis zu 12 Stunden
konfiguriert werden (System → Administration → Session-Einstellungen).

---

## Admin-Server Web-UI

### Dashboard-Funktionen

- **Übersicht** aller registrierten Pis: Status (online/offline/pause/login erforderlich), aktueller Slot, IP, Intervall
- **Pro Spur:**
  - Bildordner-Pfad ändern
  - Intervall ändern
  - **Website-URL** ändern
  - **Slots** konfigurieren (z.B. `website, kennzahlen`)
  - Pause / Fortsetzen
  - Manuell zum nächsten Slot wechseln
  - Bilder hochladen (werden direkt an den Pi übertragen)
  - **TV ein-/ausschalten** via HDMI-CEC
- **Global:**
  - Alle gleichzeitig pausieren / fortsetzen
  - Intervall für alle Pis gleichzeitig ändern
  - **Alle TVs ein-/ausschalten** via HDMI-CEC

### Status-Badges

| Badge | Bedeutung |
|---|---|
| Grün – Online | Pi läuft normal |
| Gelb – Pause | Bildwechsel angehalten |
| Lila – Login erforderlich | Website-Session abgelaufen, einmalige Anmeldung nötig |
| Rot – Offline | Pi nicht erreichbar |

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
| `pause` | – | Slot-Wechsel anhalten |
| `resume` | – | Slot-Wechsel fortsetzen |
| `next_image` | – | Manuell zum nächsten Slot wechseln |
| `set_image` | `image_index: int` | Direkt zu Slot-Index springen |
| `set_interval` | `interval_seconds: int` | Wechselintervall setzen |
| `set_folder` | `image_folder: str` | Bildordner-Pfad ändern |
| `reload_images` | – | Bilder aus Ordner neu laden |
| `set_website_url` | `website_url: str` | Website-URL ändern |
| `set_slots` | `slots: list[str]` | Slot-Reihenfolge ändern |
| `tv_on` | – | TV via HDMI-CEC einschalten |
| `tv_off` | – | TV via HDMI-CEC ausschalten (Standby) |

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

## TV-Steuerung via HDMI-CEC

Der Raspberry Pi kann den angeschlossenen TV über **HDMI-CEC** ein- und ausschalten.
Das Paket `cec-utils` wird beim Client-Setup automatisch installiert.

### Voraussetzung

HDMI-CEC muss im TV aktiviert sein – je nach Hersteller heißt es anders:

| Hersteller | Bezeichnung |
|---|---|
| Samsung | Anynet+ |
| LG | SimpLink |
| Sony | BRAVIA Sync |
| Philips | EasyLink |

### Manuell testen

```bash
# TV einschalten
echo "on 0" | cec-client -s -d 1

# TV ausschalten (Standby)
echo "standby 0" | cec-client -s -d 1

# Angeschlossene CEC-Geräte scannen
echo "scan" | cec-client -s -d 1
```

### Steuerung über Admin-Panel

- **Pro Client:** Buttons "TV ein" / "TV aus" in der Client-Card
- **Global:** Buttons "Alle TVs ein" / "Alle TVs aus" im globalen Aktionsbereich

---

## NTP-Synchronisation

**Wichtig:** Für synchronen Slot-Wechsel müssen alle Pis NTP-synchronisiert sein.

Status prüfen:
```bash
timedatectl status
```

Falls NTP nicht aktiv:
```bash
sudo timedatectl set-ntp true
```

Der Algorithmus: `(int(time.time()) // interval_seconds) % len(slots)` bestimmt,
welcher Slot angezeigt wird. Da alle Pis dieselbe UTC-Zeit verwenden, wechseln
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
- Der Slot-Wechsel ist auf ±1 Sekunde genau (abhängig von NTP-Drift).
- `command_receiver.py` muss auf jedem Display-Pi laufen (Port 8081),
  damit Direktbefehle sofort ankommen. Alternativ werden Befehle beim nächsten
  Polling-Zyklus (alle 2 Sekunden) abgeholt.
- Website-Sessions (z.B. SAP Analytics Cloud) laufen nach Timeout ab. Der Pi erkennt
  dies automatisch und meldet den Status "Login erforderlich" im Dashboard.

---

## Entwicklung

Lokales Testen (z.B. auf dem Entwicklungsrechner):

```bash
# Admin-Server lokal starten
cd server
pip install -r requirements.txt
python main.py
# → http://localhost:8080

# Command-Receiver (für Bildanzeige-Endpunkte) lokal starten
cd client
pip install -r requirements.txt
python command_receiver.py
# → http://localhost:8081/display/image/kennzahlen

# Display-Client (benötigt Chromium + X11-Display):
cd client
python display.py
```
