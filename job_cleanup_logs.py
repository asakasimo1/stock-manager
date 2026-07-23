"""
로그 파일 정리 잡 — 매일 08:00 KST (GitHub Actions 스케줄)

대상 파일: monitor_036030.log
정책: 3일 이상 지난 로그 항목 → 핵심 10줄 요약으로 대체 후 삭제
"""

from __future__ import annotations
import os, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST        = timezone(timedelta(hours=9))
KEEP_DAYS  = 3      # 이 일수 이내는 전체 보존
SUMMARY_N  = 10     # 오래된 구간 요약 줄 수
# 요약에 남길 핵심 키워드
KEY_WORDS  = ["시작", "매도 완료", "목표 도달", "오류", "장 마감", "목표 미달성", "종료"]

LOG_FILES  = [
    "monitor_036030.log",
    "profit_buy_cloud.log",
    "profit_sell_cloud.log",
    "cycle_cloud.log",
]


def _parse_dt(line: str) -> datetime | None:
    """로그 줄에서 datetime 파싱. 파싱 불가 시 None."""
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_key_line(line: str) -> bool:
    return any(k in line for k in KEY_WORDS)


def cleanup(log_path: Path):
    if not log_path.exists():
        logger.info("%s 없음 — 건너뜀", log_path.name)
        return

    cutoff = datetime.now(KST).replace(tzinfo=None) - timedelta(days=KEEP_DAYS)
    lines  = log_path.read_text(encoding="utf-8").splitlines(keepends=True)

    old_lines    = []
    recent_lines = []

    for line in lines:
        # 이미 요약 헤더인 줄은 recent로 유지 (중복 요약 방지)
        if line.startswith("# ["):
            recent_lines.append(line)
            continue
        dt = _parse_dt(line)
        if dt and dt < cutoff:
            old_lines.append(line)
        else:
            recent_lines.append(line)

    if not old_lines:
        logger.info("%s 정리 대상 없음 (3일 이내 로그만 존재)", log_path.name)
        return

    # 오래된 구간에서 핵심 줄 추출
    key_lines = [l for l in old_lines if _is_key_line(l)]

    if not key_lines:
        key_lines = old_lines  # 핵심 키워드 없으면 전체에서 샘플

    if len(key_lines) > SUMMARY_N:
        # 앞 3줄 + 뒤 7줄
        summary = key_lines[:3] + key_lines[-(SUMMARY_N - 3):]
    else:
        summary = key_lines[:SUMMARY_N]

    period_start = old_lines[0][:10]
    period_end   = old_lines[-1][:10]
    header = (f"# [{period_start} ~ {period_end}] 이전 로그 요약"
              f" ({len(old_lines)}줄 → {len(summary)}줄)\n")

    new_content = header + "".join(summary) + "\n" + "".join(recent_lines)
    log_path.write_text(new_content, encoding="utf-8")

    logger.info("%s 정리 완료: 구간 %s~%s  %d줄 → 요약 %d줄 + 최근 %d줄",
                log_path.name, period_start, period_end,
                len(old_lines), len(summary), len(recent_lines))


def main():
    logger.info("=== 로그 정리 시작 (기준: %d일 이상 경과) ===", KEEP_DAYS)
    for name in LOG_FILES:
        cleanup(Path(name))
    logger.info("=== 로그 정리 완료 ===")


if __name__ == "__main__":
    main()
