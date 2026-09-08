#!/bin/bash
# trader/ 하위 실거래 데몬 배포 트리거 — 로컬에서 커밋된 변경사항을 GitHub에
# push하고, Oracle VM에게 pull + 데몬 3개(coin/stock/scalp) 재시작을 원격으로
# 지시한다.
#
# 프론트엔드(Vercel)는 deploy.sh(이 파일과 별개) 또는 push 시 자동 배포됨 —
# 이 스크립트는 trader/ 데몬 배포 전용.
#
# 2026-09-01: stock-trader 저장소 통합 이관 때 신설(personal_ai_brain의
# deploy.sh/deploy_pull.sh 패턴 참고).

REMOTE="ubuntu@158.180.84.109"  # oracle-vm
KEY="$HOME/.ssh/ssh-key-2026-04-19.key"
SSH="ssh -i $KEY"

if [ -n "$(git status --porcelain)" ]; then
  echo "⚠ 커밋 안 된 변경사항이 있습니다. 먼저 커밋해주세요:"
  git status --short
  exit 1
fi

echo "▶ GitHub로 push..."
git push origin main

echo ""
echo "▶ Oracle VM에 pull + 데몬 재시작 지시..."
$SSH "$REMOTE" 'bash /home/ubuntu/stock-manager/trader/deploy/deploy_pull.sh'
