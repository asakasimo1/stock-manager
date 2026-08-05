"""
매수 추천 → 자동매수 잡 등록 (GitHub Actions에서 09:01/11:00/14:00 KST 크론 실행)

KOSPI+KOSDAQ TOP5를 스캔해 Gist profit_buy_jobs.json에 시장가 매수 잡으로 등록한다.
등록된 잡은 stock-trader VM 데몬(job_profit_buy_cloud.py, 30초 폴링)이
읽어서 실제 매수를 실행한다 — 이 스크립트는 잡 등록까지만 담당한다.

pykrx(KRX 데이터 조회)가 최근 KRX_ID/KRX_PW 로그인을 요구하도록 바뀌어
(Gist writer/KIS 실행과 무관하게 GHA 러너에서도 동일하게 요구됨) config에
해당 시크릿이 필요하다. VM에서 상시 프로세스로 돌리지 않고 GHA 크론으로
실행하는 이유는 factor_engine.py와 동일한 패턴을 재사용하기 위함이다.

09:01로 첫 실행을 잡은 이유: 08:50은 NXT 프리마켓(08:00~08:50) 종료 경계와
겹쳐 NXT 미대상 종목이 "NXT 시장 거래 불가" 오류로 실패할 수 있음을 실측으로
확인함 — 정규장이 확실히 열린 뒤로 옮김.

당일 이미 active/done 상태로 등록된 티커는 재등록하지 않는다
(11:00/14:00 재스캔 시 이미 산 종목이 다시 추천되어도 중복매수 방지).
"""
import logging
from datetime import datetime

import config  # noqa: F401  (모듈 임포트 시점에 환경변수 로드 확인용)
from modules import buy_signal, us_market
from modules.gist_writer import _read_gist, _write_gist
from modules.telegram_bot import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

AUTO_BUY_AMOUNT = 50_000  # 종목당 자동매수 금액(원)
TOP_N = 5


def _send(msg: str):
    try:
        if not send_message(msg):
            log.warning("텔레그램 전송 실패")
    except Exception as e:
        log.error(f"텔레그램 전송 오류: {e}")


def _register_buy_jobs(picks: list, tag: str):
    """picks 상위 TOP_N을 profit_buy_jobs.json에 시장가 매수 잡으로 등록.
    당일 이미 active/done인 티커는 건너뛴다."""
    today = datetime.now().strftime("%Y-%m-%d")
    jobs = _read_gist("profit_buy_jobs.json")
    if not isinstance(jobs, list):
        jobs = []

    already = {
        j.get("ticker") for j in jobs
        if j.get("status") in ("active", "done")
        and str(j.get("created_at") or j.get("executed_at") or "")[:10] == today
    }

    registered = []
    for p in picks[:TOP_N]:
        ticker = p.get("ticker")
        if not ticker or ticker in already:
            continue
        # 동일 티커의 기존 active 잡이 있으면 교체
        jobs = [j for j in jobs if not (j.get("ticker") == ticker and j.get("status") == "active")]
        jobs.insert(0, {
            "ticker":         ticker,
            "name":           p.get("name", ticker),
            "condition_type": "market",
            "amount":         AUTO_BUY_AMOUNT,
            "status":         "active",
            "created_at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source":         f"auto_pick_{tag}",
        })
        registered.append(f"{p.get('name', ticker)}({ticker})")

    if registered:
        ok = _write_gist({"profit_buy_jobs.json": jobs})
        log.info("자동매수 잡 등록 %s [%s]: %s", "완료" if ok else "실패", tag, registered)
        if ok:
            _send(
                f"🤖 <b>자동매수 등록</b> ({tag})\n"
                + "\n".join(f"  • {r}" for r in registered)
                + f"\n  종목당 {AUTO_BUY_AMOUNT:,}원 시장가"
            )
    else:
        log.info("자동매수 대상 없음 (전부 당일 처리 완료) [%s]", tag)


def _scan_top5() -> list:
    us_data = us_market.get_us_market()
    return buy_signal.scan(
        us_market=us_data,
        markets=["KOSPI", "KOSDAQ"],
        top_volume=100,
        min_amount_billion=30.0,
        min_vol_ratio=1.5,
        min_body_ratio=0.4,
        include_supply=True,
        top_n=TOP_N,
    )


def main(tag: str):
    if datetime.now().weekday() >= 5:
        log.info("주말 — 스캔 건너뜀")
        return
    log.info("🎯 매수 추천 스캔 시작 [%s]", tag)
    try:
        picks = _scan_top5()
        log.info("스캔 결과: %s", [(p.get("name"), p.get("ticker")) for p in picks])
        _register_buy_jobs(picks, tag)
    except Exception as e:
        log.error("스캔 실패 [%s]: %s", tag, e, exc_info=True)


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        log.info("테스트 모드 — 주말 체크 없이 즉시 1회 스캔+등록")
        picks = _scan_top5()
        log.info("스캔 결과: %s", [(p.get("name"), p.get("ticker")) for p in picks])
        _register_buy_jobs(picks, "test")
    else:
        # GitHub Actions에서 08:50/11:00/14:00 KST 크론으로 1회씩 실행됨
        tag = datetime.now().strftime("%H%M")
        main(tag)
