"""
38.co.kr(38커뮤니케이션) 전용 HTTP 헬퍼.

38.co.kr 서버는 1024비트 DH 파라미터를 쓰는 낡은 TLS 설정이라, OpenSSL 3.x의 기본
보안수준(SECLEVEL=2)에서는 핸드셰이크가 거부된다.
  - requests/urllib3 (Python 3.12, GitHub Actions 러너): SSLError [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]
  - curl / openssl s_client: dh key too small

그래서 이 호스트에만 SECLEVEL=1로 낮춘 SSLContext를 붙인 HTTPAdapter를 쓴다.
인증서 검증(check_hostname=True, CERT_REQUIRED)은 OpenSSL 기본값 그대로 유지한다 —
보안수준을 낮추는 범위는 DH 파라미터 길이 하나뿐이고, 평문 전송으로 내려가지 않는다.
그래도 SSLError가 나면(서버가 https를 막는 경우 등) http로 한 번 더 시도한다 —
공개 청약 일정 페이지라 민감 정보가 없다.
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

HOST = "www.38.co.kr"
BASE_URL = f"https://{HOST}"
CIPHERS = "DEFAULT@SECLEVEL=1"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


class LegacyTLSAdapter(HTTPAdapter):
    """보안수준만 한 단계(SECLEVEL=1) 낮춘 TLS 컨텍스트를 쓰는 어댑터."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers(CIPHERS)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_session = None


def session() -> requests.Session:
    """38.co.kr용 세션(싱글턴). https 요청에만 레거시 TLS 어댑터를 붙인다."""
    global _session
    if _session is None:
        s = requests.Session()
        s.mount(BASE_URL, LegacyTLSAdapter())
        _session = s
    return _session


def get(url: str, headers: dict | None = None, timeout: int = 15) -> requests.Response:
    """38.co.kr GET — https(SECLEVEL=1) 우선, SSLError면 http 폴백."""
    hdrs = headers if headers is not None else DEFAULT_HEADERS
    try:
        return session().get(url, headers=hdrs, timeout=timeout)
    except requests.exceptions.SSLError as e:
        print(f"[HTTP38] https 핸드셰이크 실패({e}) → http로 재시도")
        return requests.get(url.replace("https://", "http://", 1), headers=hdrs, timeout=timeout)
