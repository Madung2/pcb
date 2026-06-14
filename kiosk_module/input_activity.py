"""
설치 PC 전역 키보드·마우스 활동 시각 추적 (유휴 시간 판단용).

macOS는 시스템 설정 → 개인정보 보호 및 보안 → 접근성에서 터미널/Python 허용이 필요할 수 있습니다.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class InputActivityTracker:
    """마지막 입력 시각을 스레드 안전하게 기록합니다."""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._lock = threading.Lock()
        self._last_mono = time.monotonic()
        self._mouse_listener = None
        self._keyboard_listener = None
        self.on_activity: Optional[Callable[[], None]] = None

    def _mark(self) -> None:
        with self._lock:
            self._last_mono = time.monotonic()
        if self.on_activity:
            try:
                self.on_activity()
            except Exception as e:
                logger.error(f"on_activity 콜백 에러: {e}")

    def start(self) -> None:
        if not self._enabled:
            logger.info(
                f"INPUT_MONITOR_ENABLED=false — 유휴 기반 자동 도어 닫기는 사용할 수 없습니다."
            )
            return

        try:
            from pynput import keyboard, mouse
        except ImportError as e:
            logger.warning(f"pynput 사용 불가 ({e}). 입력 추적을 끕니다.")
            self._enabled = False
            return

        self._mouse_listener = mouse.Listener(
            on_move=lambda *_a, **_k: self._mark(),
            on_click=lambda *_a, **_k: self._mark(),
            on_scroll=lambda *_a, **_k: self._mark(),
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=lambda *_a, **_k: self._mark(),
            on_release=lambda *_a, **_k: self._mark(),
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()
        logger.info(f"키보드·마우스 입력 추적 시작")

    def stop(self) -> None:
        for lst in (self._mouse_listener, self._keyboard_listener):
            if lst is not None:
                try:
                    lst.stop()
                except Exception as e:
                    logger.debug(f"리스너 중지: {e}")
        self._mouse_listener = None
        self._keyboard_listener = None

    def seconds_since_activity(self) -> float:
        """마지막 키보드/마우스 이벤트 이후 경과 시간(초).

        추적이 꺼져 있으면 항상 ``0.0``을 반환해 '방금 입력 있음'으로 취급합니다
        (유휴 도어 닫기가 잘못 동작하지 않도록).
        """
        if not self._enabled:
            return 0.0
        with self._lock:
            return time.monotonic() - self._last_mono
