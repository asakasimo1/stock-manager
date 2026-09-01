#!/bin/bash
# ⚠️ 2026-09-01: 이 스크립트는 이관 전(stock-trader 저장소 시절) 기준으로
# 작성돼 실제 배포 경로(venv 없이 시스템 python3, /home/ubuntu/stock-trader)와도
# 이미 어긋나 있던 상태였고, 지금은 /home/ubuntu/stock-manager/trader로 이관까지
# 됐음 — 그대로 실행하지 말 것. 신규 VM 재구축 시엔 이 파일을 참고만 하고
# 실제 배포/재시작은 trader/deploy/deploy_pull.sh 사용할 것.
#
# Oracle Cloud VM — daemon_coin systemd 서비스 설치 (구버전, 참고용)
# 실행: sudo bash /opt/stock-trader/deploy/install_service.sh

set -e

APP_DIR="/opt/stock-trader"
PYTHON="$APP_DIR/venv/bin/python"
SERVICE_FILE="/etc/systemd/system/daemon-coin.service"
SERVICE_USER="${SUDO_USER:-ubuntu}"

echo "======================================"
echo "  daemon-coin 서비스 설치"
echo "  앱 경로: $APP_DIR"
echo "  실행 유저: $SERVICE_USER"
echo "======================================"

# ── 1. 최신 코드 pull ──────────────────────
echo "[1/4] 코드 업데이트..."
cd "$APP_DIR"
git pull

# ── 2. 패키지 업데이트 ─────────────────────
echo "[2/4] 패키지 설치..."
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ── 3. systemd 유닛 파일 생성 ──────────────
echo "[3/4] systemd 서비스 등록..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Coin Auto Trading Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYTHON $APP_DIR/daemon_coin.py
Restart=always
RestartSec=10
StandardOutput=append:$APP_DIR/daemon_coin.log
StandardError=append:$APP_DIR/daemon_coin.log

[Install]
WantedBy=multi-user.target
EOF

# ── 4. 서비스 활성화 & 시작 ────────────────
echo "[4/4] 서비스 시작..."
systemctl daemon-reload
systemctl enable daemon-coin
systemctl restart daemon-coin

echo ""
echo "======================================"
echo "  설치 완료!"
echo ""
echo "  상태 확인: sudo systemctl status daemon-coin"
echo "  로그 보기: tail -f $APP_DIR/daemon_coin.log"
echo "  재시작:   sudo systemctl restart daemon-coin"
echo "  중지:     sudo systemctl stop daemon-coin"
echo "======================================"

systemctl status daemon-coin --no-pager
