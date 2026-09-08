#!/bin/bash
# Oracle VM에서 실행 — GitHub(stock-manager)의 최신 커밋을 pull해서 trader/
# 하위 데몬 3개(coin-daemon/stock-daemon/scalp-daemon)를 재시작.
#
# 2026-09-01: stock-trader 저장소를 trader/ 하위로 통합 이관하면서 신설.
# stock-trader에는 이런 반복가능한 배포 스크립트가 없어(설치용 deploy/setup.sh
# /install_*.sh만 있었음) 지금까지 수동 배포였던 것을 이 기회에 정식화함.
# 이 저장소 루트에는 frontend(Vercel) 전용 deploy.sh가 이미 있어서 이름 충돌을
# 피하려고 trader/deploy/ 밑에 따로 둠.
#
# VM에서 직접 실행:
#   bash /home/ubuntu/stock-manager/trader/deploy/deploy_pull.sh
# 로컬에서 원격 트리거:
#   ssh oracle-vm "bash /home/ubuntu/stock-manager/trader/deploy/deploy_pull.sh"
set -e
cd /home/ubuntu/stock-manager

echo "▶ git pull..."
BEFORE=$(git rev-parse HEAD)
git pull origin main
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
  echo "  변경 없음 (이미 최신)"
else
  echo "  $BEFORE..$AFTER 반영"
fi

echo ""
echo "▶ 데몬 3개 재시작 (systemd)..."
for svc in coin-daemon stock-daemon scalp-daemon; do
  sudo systemctl restart "$svc"
  sleep 2
  if sudo systemctl is-active --quiet "$svc"; then
    echo "  $svc: OK"
  else
    echo "  $svc: FAILED"
  fi
done

echo ""
echo "✓ 배포 완료 (HEAD: $(git rev-parse --short HEAD))"
