from __future__ import annotations

from typing import Protocol


class WebViewController(Protocol):
    """PCB 이벤트에서 호출할 웹뷰 제어 최소 인터페이스."""

    def show_meet_web(self, url: str | None = None) -> None:
        """가이드/미트용 웹 페이지를 표시한다."""

    def restore_default_screen(self) -> None:
        """기본 키오스크 화면 또는 검은 화면으로 복귀한다."""
