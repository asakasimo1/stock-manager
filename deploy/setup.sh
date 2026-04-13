#!/bin/bash
# Oracle Cloud VM 초기 셋업 스크립트
# Ubuntu 22.04 기준
# 실행: bash setup.sh

set -e
echo "======================================"
echo "  stock-trader 서버 셋업 시작"
echo "======================================"

# ── 1. 시스템 업데이트 ─────────────────────
echo "[1/6] 시스템 업데이트..."
sudo apt-get update -y && sudo apt-get upgrade -y

# ── 2. Python 3.12 설치 ────────────────────
echo "[2/6] Python 3.12 설치..."
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3.12-distutils
curl -sS https://bootstrap.pypa.io/get-pip.py | sudo python3.12

# ── 3. Git 설치 및 코드 클론 ───────────────
echo "[3/6] 코드 클론..."
sudo apt-get install -y git
cd /opt
sudo git clone https://github.com/asakasimo1/stock-trader.git
sudo chown -R $USER:$USER /opt/stock-trader

# ── 4. 가상환경 및 패키지 설치 ─────────────
echo "[4/6] Python 패키지 설치..."
cd /opt/stock-trader
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install finance-datareader apscheduler

# ── 5. .env 파일 생성 안내 ──────────────────
echo "[5/6] .env 설정 필요..."
if [ ! -f /opt/stock-trader/.env ]; then
    cp /opt/stock-trader/.env.example /opt/stock-trader/.env
    echo ""
    echo "  ⚠  /opt/stock-trader/.env 파일을 편집하세요:"
    echo "     nano /opt/stock-trader/.env"
    echo ""
fi

# ── 6. 데이터 디렉토리 생성 ─────────────────
echo "[6/6] 디렉토리 생성..."
mkdir -p /opt/stock-trader/data/results

echo ""
echo "======================================"
echo "  셋업 완료!"
echo ""
echo "  다음 단계:"
echo "  1. nano /opt/stock-trader/.env  (API 키 입력)"
echo "  2. sudo bash /opt/stock-trader/deploy/install_service.sh"
echo "======================================"
