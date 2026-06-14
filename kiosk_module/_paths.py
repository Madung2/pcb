"""런타임 리소스 경로 헬퍼.

세 가지 경우를 통일된 인터페이스로 해결한다:

1. **개발 모드 (`uv run python ...`)** — 소스 트리에서 직접 실행.
   - 번들 리소스 루트 = 레포 루트 (`kiosk_module/` 의 상위 디렉터리)
   - 사용자 데이터 루트 = 같은 레포 루트 (`.env` 가 레포에 들어있음)

2. **PyInstaller `--onefile`** — 실행 파일 1개로 묶인 상태.
   - `sys.frozen=True`, `sys._MEIPASS` = 임시 추출 디렉터리 (rebuild·재실행마다 바뀜)
   - 번들 리소스 루트 = `sys._MEIPASS`
   - 사용자 데이터 루트 = 실행 파일과 같은 디렉터리 (`Path(sys.executable).parent`).
     이 위치에 `.env` 등 사용자가 편집할 수 있는 파일을 둔다.

3. **PyInstaller `--onedir`** — `dist/<name>/` 폴더.
   - `sys.frozen=True`, `sys._MEIPASS` = `_internal` 또는 dist 폴더
   - 사용자 데이터 = 실행 파일 옆 (`--onefile` 과 동일 처리)

첫 실행 시 번들된 디폴트 `.env` 가 사용자 디렉터리에 없으면 1회 복사한다.
이후엔 GUI 가 사용자 디렉터리의 `.env` 만 읽고/쓴다.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# kiosk_module 패키지의 상위 = 개발 모드의 레포 루트
_DEV_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """PyInstaller 등으로 묶여 실행 중인지."""
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """번들 리소스 루트.

    - frozen: ``sys._MEIPASS`` 또는 실행 파일 옆 ``_internal`` 디렉터리
    - dev: 레포 루트
    """
    if is_frozen():
        # --onefile 은 sys._MEIPASS 사용. --onedir 도 동일하게 노출됨.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # 일부 환경에서 _MEIPASS 가 비어있을 수 있음 → 실행 파일 옆 fallback
        return Path(sys.executable).resolve().parent
    return _DEV_ROOT


def user_data_root() -> Path:
    """사용자가 편집·캐시할 수 있는 디렉터리. 없으면 만든다.

    frozen 환경에서는 실행 파일과 같은 디렉터리(``<...>/JDoneKiosk.exe`` 옆).
    개발 모드에서는 레포 루트(기존 동작 유지).
    """
    if is_frozen():
        root = Path(sys.executable).resolve().parent
    else:
        root = _DEV_ROOT
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("사용자 데이터 디렉터리 생성 실패: %s", root)
    return root


def bundled_resource(*relative_parts: str) -> Path | None:
    """번들 안에 들어있는 리소스 경로. 존재하지 않으면 ``None``."""
    p = bundled_root().joinpath(*relative_parts)
    return p if p.exists() else None


def user_env_path() -> Path:
    """사용자 ``.env`` 위치 (편집 가능). 존재 여부는 보장하지 않는다."""
    return user_data_root() / ".env"


def ensure_user_env() -> Path:
    """사용자 ``.env`` 가 없으면 번들된 디폴트로 초기화하고 경로를 반환한다.

    번들 디폴트 후보 우선순위:
      1. ``default.env`` (빌드시 명시적으로 넣은 템플릿)
      2. ``.env`` (개발 모드에서 레포에 있던 그대로)
    """
    user_path = user_env_path()
    if user_path.exists():
        return user_path

    candidates = (
        bundled_resource("default.env"),
        bundled_resource(".env"),
    )
    for src in candidates:
        if src is None or not src.is_file():
            continue
        try:
            shutil.copyfile(src, user_path)
            logger.info("사용자 .env 초기화: %s → %s", src, user_path)
            return user_path
        except Exception:
            logger.exception("사용자 .env 초기화 실패: %s → %s", src, user_path)

    # 디폴트가 번들에 없으면 빈 .env 라도 만들어 둔다 (편집 가능 상태 유지)
    try:
        user_path.write_text("# JDONE Kiosk .env\n", encoding="utf-8")
        logger.info("사용자 .env 빈 파일 생성: %s", user_path)
    except Exception:
        logger.exception("사용자 .env 빈 파일 생성 실패: %s", user_path)
    return user_path


def resolve_runtime_file(
    candidate: str,
    *,
    bundled_name: str | None = None,
) -> Path | None:
    """런타임에 ``candidate`` 경로(상대/절대)를 해석.

    탐색 순서:
      1. ``candidate`` 절대경로면 그대로
      2. 사용자 디렉터리 + ``candidate``
      3. 번들 리소스 + ``candidate``
      4. 번들 리소스 + ``bundled_name`` (지정된 경우)
    """
    s = (candidate or "").strip()
    if s:
        p = Path(s)
        if p.is_absolute() and p.is_file():
            return p
        if not p.is_absolute():
            ud = user_data_root() / p
            if ud.is_file():
                return ud
            br = bundled_root() / p
            if br.is_file():
                return br
    if bundled_name:
        br2 = bundled_resource(bundled_name)
        if br2 is not None and br2.is_file():
            return br2
    return None
