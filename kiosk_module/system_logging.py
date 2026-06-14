"""사용자 데이터 디렉터리에 공통 시스템 로그 파일을 기록한다.

GUI·WebView 자식 프로세스·백그라운드 워커가 같은 ``kiosk_system.log`` 에 append 한다.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ._paths import user_data_root

SYSTEM_LOG_FILENAME = "kiosk_system.log"
_HANDLER_ATTR = "_kiosk_system_file_handler"


def system_log_path() -> Path:
    """사용자 데이터 루트의 시스템 로그 파일 경로."""
    return user_data_root() / SYSTEM_LOG_FILENAME


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        from .config import config

        return getattr(logging, (config.log_level or "INFO").upper(), logging.INFO)
    if isinstance(level, str):
        return getattr(logging, level.upper(), logging.INFO)
    return int(level)


def setup_system_logging(
    *,
    role: str = "app",
    level: str | int | None = None,
) -> Path:
    """루트 로거에 파일 핸들러를 붙이고(중복 방지), 세션 시작 줄을 남긴다."""
    path = system_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if not any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers):
        handler = logging.FileHandler(path, encoding="utf-8", mode="a")
        setattr(handler, _HANDLER_ATTR, True)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] [pid=%(process)d] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)

    lvl = _resolve_level(level)
    root.setLevel(lvl)

    logging.getLogger(__name__).info(
        "=== 시스템 로그 세션 시작 role=%s pid=%d exe=%s log=%s ===",
        role,
        os.getpid(),
        sys.executable,
        path,
    )
    return path


def read_log_tail(path: Path | None = None, *, max_lines: int = 40) -> list[str]:
    """로그 파일 마지막 ``max_lines`` 줄."""
    p = path or system_log_path()
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines[-max_lines:] if ln.strip()]


def read_log_from_offset(path: Path, offset: int) -> tuple[str, int]:
    """``offset`` 이후 신규 내용과 새 오프셋."""
    if not path.is_file():
        return "", offset
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read()
            return chunk, fh.tell()
    except OSError:
        return "", offset
