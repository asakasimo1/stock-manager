#!/bin/bash
# Oracle Cloud VM — daemon_scalp systemd 서비스 설치 (초단타 스캘핑, 기존 daemon-coin과 별도 프로세스)
# 실행: sudo bash /opt/stock-trader/deploy/install_scalp_service.sh

set -e

APP_DIR="/opt/stock-trader"
PYTHON="$APP_DIR/venv/bin/python"
SERVICE_FILE="/etc/systemd/system/daemon-scalp.service"
SERVICE_USER="${SUDO_USER:-ubuntu}"

echo "======================================"
echo "  daemon-scalp 서비스 설치"
echo "  앱 경로: $APP_DIR"
echo "  실행 유저: $SERVICE_USER"
echo "======================================"

echo "[1/4] 최신 코드 pull..."
cd "$APP_DIR"
git pull

echo "[2/4] 패키지 설치..."
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "[3/4] systemd 서비스 등록..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Scalp (Ultra-Short-Term) Auto Trading Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYTHON $APP_DIR/daemon_scalp.py
Restart=always
RestartSec=10
StandardOutput=append:$APP_DIR/daemon_scalp.log
StandardError=append:$APP_DIR/daemon_scalp.log

[Install]
WantedBy=multi-user.target
EOF

echo "[4/4] 서비스 시작..."
systemctl daemon-reload
systemctl enable daemon-scalp
systemctl restart daemon-scalp

echo ""
echo "======================================"
echo "  설치 완료!"
echo ""
echo "  상태 확인: sudo systemctl status daemon-scalp"
echo "  로그 보기: tail -f $APP_DIR/daemon_scalp.log"
echo "  재시작:   sudo systemctl restart daemon-scalp"
echo "  중지:     sudo systemctl stop daemon-scalp"
echo "======================================"

systemctl status daemon-scalp --no-pager
