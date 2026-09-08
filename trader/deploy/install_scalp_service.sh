#!/bin/bash
# Oracle Cloud VM — scalp-daemon systemd 서비스 설치 (초단타 스캘핑, 기존 coin-daemon/stock-daemon과 별도 프로세스)
# 실행: sudo bash /home/ubuntu/stock-manager/trader/deploy/install_scalp_service.sh
#
# 실제 배포 경로는 /home/ubuntu/stock-manager/trader (venv 없이 시스템 python3 사용) —
# coin-daemon.service / stock-daemon.service 와 동일한 구성을 따른다.

set -e

APP_DIR="/home/ubuntu/stock-manager/trader"
PYTHON="/usr/bin/python3"
SERVICE_FILE="/etc/systemd/system/scalp-daemon.service"
SERVICE_USER="${SUDO_USER:-ubuntu}"

echo "======================================"
echo "  scalp-daemon 서비스 설치"
echo "  앱 경로: $APP_DIR"
echo "  실행 유저: $SERVICE_USER"
echo "======================================"

echo "[1/3] systemd 서비스 등록..."
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

echo "[2/3] 서비스 시작..."
systemctl daemon-reload
systemctl enable scalp-daemon
systemctl restart scalp-daemon

echo "[3/3] 상태 확인"
echo ""
echo "======================================"
echo "  설치 완료!"
echo ""
echo "  상태 확인: sudo systemctl status scalp-daemon"
echo "  로그 보기: tail -f $APP_DIR/daemon_scalp.log"
echo "  재시작:   sudo systemctl restart scalp-daemon"
echo "  중지:     sudo systemctl stop scalp-daemon"
echo "======================================"

systemctl status scalp-daemon --no-pager
