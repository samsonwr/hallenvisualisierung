#!/usr/bin/env bash
# =============================================================================
# setup.sh – Hallenvisualisierung Setup-Script
# Raspberry Pi OS Bookworm (64-bit), Python 3.11+
#
# Verwendung:
#   Auf einem Display-Pi:   sudo bash setup.sh --mode client --spur-name Spur-01 --server-url http://192.168.1.100:8080
#   Auf dem Admin-Server:   sudo bash setup.sh --mode server
# =============================================================================
set -euo pipefail

# ---- Farben ----
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---- Standard-Werte ----
MODE=""
SPUR_NAME="Spur-01"
SERVER_URL="http://192.168.1.100:8080"
IMAGE_FOLDER="/home/pi/spur-bilder"
INTERVAL=30
INSTALL_DIR="/home/pi/hallenvisualisierung"
VENV_DIR="${INSTALL_DIR}/venv"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Argumente parsen ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)         MODE="$2";         shift 2 ;;
    --spur-name)    SPUR_NAME="$2";    shift 2 ;;
    --server-url)   SERVER_URL="$2";   shift 2 ;;
    --image-folder) IMAGE_FOLDER="$2"; shift 2 ;;
    --interval)     INTERVAL="$2";     shift 2 ;;
    --install-dir)  INSTALL_DIR="$2";  shift 2 ;;
    -h|--help)
      echo "Verwendung: setup.sh --mode <client|server> [Optionen]"
      echo ""
      echo "  --mode client|server        Betriebsmodus (Pflicht)"
      echo "  --spur-name NAME            Name der Spur (nur client, default: Spur-01)"
      echo "  --server-url URL            Admin-Server URL (nur client, default: http://192.168.1.100:8080)"
      echo "  --image-folder PFAD         Bildordner auf dem Pi (nur client, default: /home/pi/spur-bilder)"
      echo "  --interval SEKUNDEN         Wechselintervall (nur client, default: 30)"
      echo "  --install-dir PFAD          Installationspfad (default: /home/pi/hallenvisualisierung)"
      exit 0
      ;;
    *) error "Unbekanntes Argument: $1" ;;
  esac
done

[[ -z "$MODE" ]] && error "Bitte --mode client oder --mode server angeben"
[[ "$MODE" != "client" && "$MODE" != "server" ]] && error "Ungültiger Modus: $MODE"

# ---- Root-Check ----
[[ "$EUID" -ne 0 ]] && error "Dieses Script muss als root ausgeführt werden (sudo)"

PI_USER="${SUDO_USER:-pi}"
PI_HOME=$(eval echo "~${PI_USER}")

info "=== Hallenvisualisierung Setup ==="
info "Modus:          $MODE"
info "Installationsort: $INSTALL_DIR"
info "Benutzer:       $PI_USER"

# =============================================================================
# 1. System-Pakete
# =============================================================================
info "Aktualisiere Paketlisten..."
apt-get update -qq

info "Installiere System-Pakete..."
PACKAGES="python3 python3-venv python3-pip git"
if [[ "$MODE" == "client" ]]; then
  PACKAGES="$PACKAGES chromium-browser cec-utils"
fi
apt-get install -y $PACKAGES

# Zeitsynchronisation aktivieren (wichtig für synchronen Bildwechsel)
timedatectl set-ntp true
info "Zeitsynchronisation aktiviert (systemd-timesyncd)"

# =============================================================================
# 2. Projektdateien kopieren
# =============================================================================
info "Kopiere Projektdateien nach ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp -r "${REPO_DIR}/." "${INSTALL_DIR}/"
chown -R "${PI_USER}:${PI_USER}" "${INSTALL_DIR}"

# =============================================================================
# 3. Python-Virtual-Environment
# =============================================================================
info "Erstelle Python-Virtual-Environment..."
sudo -u "${PI_USER}" python3 -m venv "${VENV_DIR}"

info "Installiere Python-Abhängigkeiten..."
if [[ "$MODE" == "client" ]]; then
  sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip -q
  sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/client/requirements.txt" -q
else
  sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip -q
  sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/server/requirements.txt" -q
fi

# =============================================================================
# 4. Konfiguration (nur Client)
# =============================================================================
if [[ "$MODE" == "client" ]]; then
  info "Erstelle Bildordner: ${IMAGE_FOLDER}"
  mkdir -p "${IMAGE_FOLDER}"
  chown -R "${PI_USER}:${PI_USER}" "${IMAGE_FOLDER}"

  CONFIG_FILE="${INSTALL_DIR}/client/config.json"
  info "Schreibe config.json..."
  cat > "${CONFIG_FILE}" <<EOF
{
  "spur_name": "${SPUR_NAME}",
  "image_folder": "${IMAGE_FOLDER}",
  "interval_seconds": ${INTERVAL},
  "server_url": "${SERVER_URL}",
  "slots": ["spur_bezeichnung", "kennzahlen"],
  "website_url": "",
  "website_username": "",
  "website_password": ""
}
EOF
  chown "${PI_USER}:${PI_USER}" "${CONFIG_FILE}"
  info "config.json geschrieben: ${CONFIG_FILE}"
fi

# =============================================================================
# 5. systemd-Services installieren
# =============================================================================
info "Installiere systemd-Services..."

if [[ "$MODE" == "client" ]]; then
  # Service-Dateien mit korrekten Pfaden anpassen und installieren
  sed "s|/home/pi/hallenvisualisierung|${INSTALL_DIR}|g; s|User=pi|User=${PI_USER}|g; s|Group=pi|Group=${PI_USER}|g" \
    "${INSTALL_DIR}/config/display.service" \
    > /etc/systemd/system/hallenvis-display.service

  sed "s|/home/pi/hallenvisualisierung|${INSTALL_DIR}|g; s|User=pi|User=${PI_USER}|g; s|Group=pi|Group=${PI_USER}|g" \
    "${INSTALL_DIR}/config/display-receiver.service" \
    > /etc/systemd/system/hallenvis-receiver.service

  systemctl daemon-reload
  systemctl enable hallenvis-display.service
  systemctl enable hallenvis-receiver.service
  info "Services aktiviert: hallenvis-display, hallenvis-receiver"
  info "Starte Services..."
  systemctl start hallenvis-receiver.service || warn "Receiver konnte nicht gestartet werden (läuft Display schon?)"

else
  # Admin-Server
  sed "s|/home/pi/hallenvisualisierung|${INSTALL_DIR}|g; s|User=pi|User=${PI_USER}|g; s|Group=pi|Group=${PI_USER}|g" \
    "${INSTALL_DIR}/config/admin-server.service" \
    > /etc/systemd/system/hallenvis-server.service

  systemctl daemon-reload
  systemctl enable hallenvis-server.service
  systemctl start hallenvis-server.service
  info "Admin-Server gestartet"
fi

# =============================================================================
# 6. Display-Client Autostart (nur Client – startet nach grafischer Sitzung)
# =============================================================================
if [[ "$MODE" == "client" ]]; then
  AUTOSTART_DIR="${PI_HOME}/.config/autostart"
  mkdir -p "${AUTOSTART_DIR}"
  cat > "${AUTOSTART_DIR}/hallenvis-display.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Hallenvisualisierung Display
Exec=${VENV_DIR}/bin/python ${INSTALL_DIR}/client/display.py
X-GNOME-Autostart-enabled=true
EOF
  chown -R "${PI_USER}:${PI_USER}" "${AUTOSTART_DIR}"
  info "Autostart-Eintrag erstellt: ${AUTOSTART_DIR}/hallenvis-display.desktop"

  # Hinweis: Autostart via systemd erfordert DISPLAY-Variable.
  # Bei Raspberry Pi OS Lite empfehlen wir den Desktop-Autostart-Eintrag oben.
  # Für Headless-Setup mit X11 den Display-Service manuell aktivieren:
  warn "Hinweis: Falls kein Desktop-Environment vorhanden, den Display-Service via:"
  warn "  sudo systemctl start hallenvis-display.service"
fi

# =============================================================================
# Fertig
# =============================================================================
echo ""
info "========================================"
info " Setup abgeschlossen! ($MODE)"
info "========================================"
if [[ "$MODE" == "client" ]]; then
  info "  Spur:     ${SPUR_NAME}"
  info "  Server:   ${SERVER_URL}"
  info "  Bilder:   ${IMAGE_FOLDER}"
  info ""
  info "  Bilder ablegen: ${IMAGE_FOLDER}/spur_bezeichnung.png"
  info "                  ${IMAGE_FOLDER}/kennzahlen.png"
  info ""
  info "  Status prüfen:  sudo systemctl status hallenvis-display"
  info "                  sudo systemctl status hallenvis-receiver"
  info "  Logs:           sudo journalctl -u hallenvis-display -f"
  info "  Neustart:       sudo systemctl restart hallenvis-display"
else
  SERVER_IP=$(hostname -I | awk '{print $1}')
  info "  Web-UI:   http://${SERVER_IP}:8080"
  info ""
  info "  Status prüfen:  sudo systemctl status hallenvis-server"
  info "  Logs:           sudo journalctl -u hallenvis-server -f"
fi
