#!/bin/bash
# Vercel 프로덕션 배포 + hsk-stockmanager.vercel.app 별칭 자동 재지정.
#
# stock-analyzer-nu-brown.vercel.app은 Vercel이 프로젝트에 강제로 붙이는
# 기본 도메인이라 배포마다 자동으로 최신을 따라가지만, hsk-stockmanager.vercel.app은
# `vercel alias set`으로 수동 지정한 별칭이라 재지정하지 않으면 배포마다 며칠씩
# 뒤처짐(2026-08-15 실측: "실시간 동기화 안 됨" 사용자 리포트로 발견). 매번
# 수동으로 alias set을 잊지 않도록 배포 스크립트에 묶어둔다.
set -euo pipefail
cd "$(dirname "$0")"

ALIAS="hsk-stockmanager.vercel.app"

echo "▶ Vercel 프로덕션 배포 중..."
# --yes(non-interactive) 모드에서는 stdout이 plain URL이 아니라 JSON으로 나옴
# (vercel CLI 54.x 실측, 2026-08-15) — deployment.url을 파싱해서 사용.
DEPLOY_JSON=$(vercel --prod --yes 2>/dev/null)
DEPLOY_URL=$(echo "$DEPLOY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['deployment']['url'])" 2>/dev/null)
[[ "$DEPLOY_URL" == http* ]] || DEPLOY_URL="https://$DEPLOY_URL"

if [[ "$DEPLOY_URL" != https://*.vercel.app ]]; then
  echo "✗ 배포 URL을 확인하지 못했습니다. 출력을 확인하세요:"
  echo "$DEPLOY_JSON"
  exit 1
fi

echo "▶ 배포 완료: $DEPLOY_URL"
echo "▶ $ALIAS 재지정 중..."
vercel alias set "$DEPLOY_URL" "$ALIAS"

echo "✓ 배포 + 별칭 재지정 완료: https://$ALIAS"
