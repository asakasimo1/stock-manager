"""
텔레그램 알림 모듈

사용 전 .env / GitHub Secrets 설정:
  TELEGRAM_BOT_TOKEN = 봇 토큰 (BotFather에서 발급)
  TELEGRAM_CHAT_ID   = 내 채팅 ID (@userinfobot 에서 확인)
"""

import os, requests, logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send(message: str):
    """텔레그램 메시지 전송. 토큰 없으면 조용히 건너뜀."""
    if not TOKEN or not CHAT_ID:
        logger.warning("텔레그램 토큰/채팅ID 없음 — 전송 건너뜀 (TOKEN=%r, CHAT_ID=%r)", TOKEN, CHAT_ID)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("텔레그램 전송 성공")
        else:
            logger.error("텔레그램 전송 실패 (HTTP %d): %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("텔레그램 전송 오류: %s", e)
