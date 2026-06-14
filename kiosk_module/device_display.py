"""
WebView 표시 · 웹 리소스 다운로드 · 로컬 fallback.
# ysoh 2026-06-14
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ._paths import user_data_root
from .config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# pywebview GUI 백엔드 선택
# ---------------------------------------------------------------------------

def _gui_backend() -> str | None:  # ysoh 2026-06-14
    """플랫폼별 pywebview GUI 백엔드를 반환합니다."""
    if sys.platform == "darwin":
        return "cocoa"
    elif sys.platform == "win32":
        return "edgechromium"
    return None


# ---------------------------------------------------------------------------
# WebView 표시
# ---------------------------------------------------------------------------

def smartpole_display(  # ysoh 2026-06-14
    url: str,
    *,
    title: str = "JDONE smartpole Display",
    fullscreen: bool = True,
    width: int = 1280,
    height: int = 800,
) -> int:
    """pywebview 로 URL 을 표시합니다 (macOS/Windows/Linux).

    Args:
        url: 표시할 웹 페이지 URL
        title: 창 제목
        fullscreen: 전체화면 여부
        width/height: 윈도우 모드 크기
    """
    import webview

    backend = _gui_backend()
    logger.info("display 시작: url=%s gui=%s fullscreen=%s", url, backend, fullscreen)

    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        resizable=True,
        fullscreen=fullscreen,
        background_color="#000000",
    )
    webview.start(
        gui=backend,
        debug=config.log_level.upper() == "DEBUG",
    )
    logger.info("display 종료")
    return 0


def kiosk_display(  # ysoh 2026-06-14
    url: str,
    *,
    title: str = "JDONE Kiosk",
    fullscreen: bool = True,
    width: int = 1280,
    height: int = 800,
) -> int:
    """키오스크 전용 WebView 표시."""
    import webview

    backend = _gui_backend()
    logger.info("display 시작: url=%s gui=%s fullscreen=%s", url, backend, fullscreen)

    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        resizable=True,
        fullscreen=fullscreen,
        background_color="#000000",
    )
    webview.start(
        gui=backend,
        debug=config.log_level.upper() == "DEBUG",
    )
    logger.info("display 종료")
    return 0


def pole_n_ed_display(  # ysoh 2026-06-14
    url: str,
    *,
    title: str = "JDONE Pole N ED",
    fullscreen: bool = True,
    width: int = 1280,
    height: int = 800,
    second_width: int = 320,
    second_height: int = 140,
) -> int:
    """POLE_N_ED 전용 듀얼 디스플레이 WebView 표시.

    - 1번째 창: 기본 URL → HDMI 1st display (전체화면)
    - 2번째 창: ``base_url + led_url`` → HDMI 2nd display (좌측상단 0,0 기준, 320x140)

    Args:
        url: 기본 송출 URL (device_id 쿼리 파라미터 포함)
        title: 1번째 창 제목
        fullscreen: 1번째 창 전체화면 여부
        width/height: 1번째 창 윈도우 모드 크기
        second_width: 2번째 창 가로 크기 (기본 320)
        second_height: 2번째 창 세로 크기 (기본 140)
    """
    import webview

    backend = _gui_backend()

    # 연결된 스크린 목록 조회
    available_screens = webview.screens
    screen_count = len(available_screens) if available_screens else 0
    logger.info(
        "POLE_N_ED display 시작: url=%s screens=%d gui=%s",
        url, screen_count, backend,
    )

    # --- 1번째 창: HDMI 1st display (전체화면) ---
    first_screen = available_screens[0] if screen_count >= 1 else None
    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        resizable=True,
        fullscreen=fullscreen,
        background_color="#000000",
        screen=first_screen,
    )

    # --- 2번째 창: HDMI 2nd display (좌측상단 0,0 기준, 320x140) ---
    url_2 = build_led_url(config.base_url, config.led_url, _device_id_from_url(url))
    if not url_2:
        url_2 = url.rstrip("/") + "_2"
    logger.info("2nd display URL: %s", url_2)

    if screen_count >= 2:
        # 2번째 모니터가 있으면 해당 스크린에 배치
        second_screen = available_screens[1]
        second_x = int(getattr(second_screen, "x", 0) or 0)
        second_y = int(getattr(second_screen, "y", 0) or 0)
        logger.info(
            "2nd display: url=%s screen=%s x=%d y=%d size=%dx%d",
            url_2, second_screen, second_x, second_y, second_width, second_height,
        )
        webview.create_window(
            title=f"{title} - 2nd",
            url=url_2,
            width=second_width,
            height=second_height,
            x=second_x,
            y=second_y,
            resizable=False,
            fullscreen=False,
            frameless=True,
            background_color="#000000",
            screen=second_screen,
        )
    else:
        # 2번째 모니터가 없으면 1번째 모니터에 윈도우로 표시
        logger.warning(
            "2nd HDMI 디스플레이 미감지 (screens=%d) → 1st 모니터에 윈도우로 표시",
            screen_count,
        )
        webview.create_window(
            title=f"{title} - 2nd",
            url=url_2,
            width=second_width,
            height=second_height,
            x=0,
            y=0,
            resizable=False,
            fullscreen=False,
            frameless=True,
            background_color="#000000",
        )

    webview.start(
        gui=backend,
        debug=config.log_level.upper() == "DEBUG",
    )
    logger.info("POLE_N_ED display 종료")
    return 0


def display_error_page(*, title: str = "JDONE") -> int:  # ysoh 2026-06-14
    """페이지 로딩 불가 오류 HTML 을 WebView 로 표시합니다."""
    import webview

    ERROR_HTML = """\
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <style>
    html,body{height:100%;margin:0;background:#111;color:#eee;
      font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Malgun Gothic",sans-serif;}
    .c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;}
    .icon{font-size:64px;margin-bottom:24px;}
    .msg{font-size:28px;font-weight:bold;margin-bottom:12px;}
    .sub{font-size:16px;opacity:0.6;}
  </style>
  <title>페이지 로딩 불가</title>
</head>
<body>
  <div class="c">
    <div class="icon">⚠️</div>
    <div class="msg">페이지를 불러올 수 없습니다</div>
    <div class="sub">네트워크 연결을 확인하고 프로그램을 다시 시작해 주세요.</div>
  </div>
</body>
</html>
"""
    webview.create_window(
        title=title,
        html=ERROR_HTML,
        fullscreen=True,
        background_color="#111111",
    )
    webview.start(gui=_gui_backend())
    return 1


def display_local_fallback(res_dir: Path, title: str) -> int:  # ysoh 2026-06-14
    """로컬에 캐시된 웹 페이지가 있으면 표시하고, 없으면 오류 HTML 을 표시합니다."""
    index = res_dir / "index.html"

    if index.is_file():
        local_url = index.as_uri()
        logger.info("로컬 캐시 웹 페이지 표시: %s", local_url)
        return smartpole_display(local_url, title=title, fullscreen=True)

    logger.error("로컬 캐시 없음 (%s) → 오류 페이지 표시", res_dir)
    return display_error_page(title=title)


# ---------------------------------------------------------------------------
# URL 접속 확인
# ---------------------------------------------------------------------------

def probe_url(url: str, timeout: float = 10.0) -> bool:  # ysoh 2026-06-14
    """URL 에 HEAD 요청으로 접속 가능 여부를 확인합니다."""
    try:
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "JDONE-Kiosk/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # 4xx/5xx 라도 서버가 응답했으면 네트워크 OK
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 웹 리소스 다운로드
# ---------------------------------------------------------------------------

def download_web_resources(url: str, dest_dir: Path) -> bool:  # ysoh 2026-06-14
    """``url`` 의 HTML 과 내부 리소스(CSS, JS, 이미지 등)를 ``dest_dir`` 에 저장합니다.

    HTML 을 받아 index.html 로 저장하고, <link>/<script>/<img> 의
    href/src 를 다운로드해 같은 폴더에 저장, 경로를 상대 경로로 치환합니다.

    Returns:
        True = 성공, False = 실패
    """
    logger.info("웹 리소스 다운로드 시작: %s → %s", url, dest_dir)

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "JDONE-Kiosk/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("HTML 다운로드 실패: %s", url)
        return False

    # 기존 리소스 폴더 비우기
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 리소스 URL 추출
    resource_pattern = re.compile(
        r'''(?:href|src|data-src)\s*=\s*["']([^"']+?)["']''',
        re.IGNORECASE,
    )
    matches = resource_pattern.findall(html)
    downloaded: dict[str, str] = {}  # original_url → local_filename

    for i, raw_ref in enumerate(matches):
        ref = raw_ref.strip()
        if (
            not ref
            or ref.startswith("data:")
            or ref.startswith("#")
            or ref.startswith("javascript:")
        ):
            continue
        abs_url = urllib.parse.urljoin(url, ref)
        if abs_url in downloaded:
            continue

        parsed = urllib.parse.urlparse(abs_url)
        filename = os.path.basename(parsed.path) or f"resource_{i}"
        if filename in downloaded.values():
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{i}{ext}"

        try:
            res_req = urllib.request.Request(
                abs_url, headers={"User-Agent": "JDONE-Kiosk/1.0"}
            )
            with urllib.request.urlopen(res_req, timeout=15) as res_resp:
                data = res_resp.read()
            (dest_dir / filename).write_bytes(data)
            downloaded[abs_url] = filename
            html = html.replace(ref, filename)
        except Exception:
            logger.debug("리소스 다운로드 스킵: %s", abs_url)

    (dest_dir / "index.html").write_text(html, encoding="utf-8")
    logger.info(
        "웹 리소스 다운로드 완료: %d 개 리소스 저장 → %s", len(downloaded), dest_dir
    )
    return True


# ---------------------------------------------------------------------------
# 리소스 캐시 디렉터리
# ---------------------------------------------------------------------------

def get_res_dir(device_type: str) -> Path:  # ysoh 2026-06-14
    """장치 타입에 따른 리소스 캐시 디렉터리를 반환합니다."""
    dir_map = {
        "SMART_POLE": "SMART_POLE_RES",
        "KIOSK": "KIOSK_RES",
        "POLE_N_ED": "POLE_N_ED_RES",
    }
    name = dir_map.get(device_type, f"{device_type}_RES")
    return user_data_root() / name


def build_device_url(base_url: str, device_id: str) -> str:  # ysoh 2026-06-14
    """base_url 과 device_id 로 최종 송출 URL 을 만듭니다."""
    base = base_url.rstrip("/")
    return f"{base}/?device_id={urllib.parse.quote(device_id)}"


def _device_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    qs = urllib.parse.parse_qs(parsed.query)
    return (qs.get("device_id") or [""])[0].strip()


def build_led_url(base_url: str, led_url: str, device_id: str) -> str:
    """base_url 과 서버 응답 led_url 을 합쳐 POLE_N_ED LED 화면 URL 을 만든다."""
    led = (led_url or "").strip()
    if not led:
        return ""
    base = (base_url or "").strip()
    if urllib.parse.urlparse(led).scheme:
        out = led
    else:
        out = urllib.parse.urljoin(base.rstrip("/") + "/", led.lstrip("/"))
    if device_id:
        parsed = urllib.parse.urlparse(out)
        qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not any(k.lower() in ("device_id", "deviceid") for k, _ in qsl):
            qsl.append(("device_id", device_id))
            out = urllib.parse.urlunparse(
                parsed._replace(query=urllib.parse.urlencode(qsl, doseq=True))
            )
    return out
