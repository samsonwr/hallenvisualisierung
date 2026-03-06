# Architektur – Hallenvisualisierung

## Uebersicht

```
┌─────────────────────────────────────────────────────────────┐
│                     Admin-Server (PC/Pi)                     │
│  FastAPI :8080 – Web-UI + REST-API                           │
│  http://<server-ip>:8080  →  Dashboard im Browser            │
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

## Komponenten

### 1. Display-Client (`client/display.py`)

- **Pygame-Vollbildanzeige** auf HDMI
- Zeigt abwechselnd `spur_bezeichnung.png` und `kennzahlen.png`
- **NTP-Synchronisation:** `int(time.time()) // interval % 2` bestimmt das Bild
- Hintergrundthread fuer Server-Kommunikation (Heartbeat + Command-Polling)
- Thread-sicherer Zugriff auf geteilten State via Lock
- Tastatur-Steuerung (ESC, Leertaste, Pfeiltaste, R)

### 2. Command Receiver (`client/command_receiver.py`)

- FastAPI-Server auf Port 8081
- Empfaengt direkte Befehle und Bilder vom Admin-Server
- Kommuniziert mit `display.py` ueber Datei (`/tmp/display_command.json`)

### 3. Admin-Server (`server/main.py`)

- FastAPI auf Port 8080
- In-Memory Client-Registry
- Befehlspufferung fuer offline Clients
- Bilder-Upload mit Weiterleitung an Clients

### 4. Web-UI (`server/static/index.html`)

- Dark-Theme Dashboard
- Client-Karten mit Status, Steuerung und Upload
- Auto-Refresh alle 5 Sekunden
- Globale Steuerung (Pause, Intervall)

## Kommunikationsfluss

### Registrierung & Heartbeat
1. Display-Client startet und registriert sich per POST `/api/clients/register`
2. Alle 10s sendet der Client einen Heartbeat per POST `/api/clients/heartbeat`
3. Server markiert Clients als offline nach 30s ohne Heartbeat

### Befehlsuebermittlung (2 Wege)
1. **Direkt:** Server sendet HTTP POST an `http://<pi-ip>:8081/command`
2. **Polling-Fallback:** Client fragt alle 2s per GET `/api/clients/{name}/commands`

### Bilder-Upload
1. Benutzer laedt Bild im Web-UI hoch
2. Server speichert lokal unter `server/uploads/{spur_name}/`
3. Server uebertraegt Bild per HTTP POST an `http://<pi-ip>:8081/upload`
4. Command Receiver speichert Bild im konfigurierten Bildordner
5. `reload_images`-Befehl wird ausgeloest

## NTP-Synchronisation

Alle Displays wechseln synchron, weil:
- Alle Pis nutzen NTP fuer Zeitsynchronisation
- Der Algorithmus `int(time.time()) // interval % 2` ergibt fuer alle
  Pis zur gleichen Sekunde denselben Wert
- Genauigkeit: ±1 Sekunde (abhaengig von NTP-Drift)
