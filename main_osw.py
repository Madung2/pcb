"""
GPS SMART KIOSK 통신 모듈 - 메인 엔트리포인트

실행 방법:
    uv sync
    uv run python main.py

.exe 빌드 (예시):
    uv add --group dev pyinstaller
    uv run pyinstaller --onefile main.py
"""

import asyncio
import logging
import sys
import time  # [추가] 재시작 대기를 위해 사용

from kiosk_module.config import config
from kiosk_module.kiosk_runner import run_kiosk
from kiosk_module.serial_manager import SerialManager


# [추가] 자동 재시작 정책 상수 — 운영 정책에 맞게 조정 가능
RESTART_INITIAL_DELAY = 5        # 첫 재시작 대기 시간(초)
RESTART_MAX_DELAY = 60           # 최대 재시작 대기 시간(초)
RESTART_BACKOFF_FACTOR = 2       # 지수 백오프 배수
MAX_CONSECUTIVE_FAILURES = 0     # 0이면 무한 재시도, N>0이면 N회 연속 실패 시 종료


def setup_logging():
    """로깅 설정."""
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def resolve_serial_port() -> str:
    """환경설정의 포트 문자열을 실제 장치 경로로 해석."""
    found = SerialManager.resolve_port_choice(
        config.serial_port,
        config.serial_port_description_keyword,
        usb_vid=config.serial_usb_vid or None,
        usb_pid=config.serial_usb_pid or None,
        usb_serial=config.serial_usb_serial or None,
    )
    if found is None:
        if config.serial_usb_vid and config.serial_usb_pid:
            raise SystemExit(
                "USB 장치 자동 검색 실패: "
                f"VID:PID={config.serial_usb_vid}:{config.serial_usb_pid}"
                f"{(' SER=' + config.serial_usb_serial) if config.serial_usb_serial else ''} "
                "에 맞는 포트가 없습니다."
            )
        kw = (config.serial_port_description_keyword or "").strip() or "USB"
        raise SystemExit(
            f"자동 포트 검색 실패: 설명에 {kw!r} 가 포함된 포트가 없습니다."
        )
    return found


async def run_cli_main(port: str) -> None:
    # [수정] run_kiosk 내부에서 발생할 수 있는 예외를 잡아 로깅 후 상위로 전달.
    #        CancelledError(정상 취소)는 흡수하지 않고 그대로 다시 발생시킴.
    logger = logging.getLogger("run_cli_main")
    try:
        await run_kiosk(
            port,
            config.serial_baudrate,
            stop_event=None,
            controller_ref=None,
        )
    except asyncio.CancelledError:
        logger.info("키오스크 실행이 취소되었습니다.")
        raise
    except Exception as e:
        # 예외 정보(트레이스백 포함)를 로그에 남기고 호출자에게 전달
        logger.exception("run_kiosk 실행 중 예외 발생: %s", e)
        raise


# [추가] CLI 모드의 핵심 동작을 한 번 실행하는 헬퍼.
#        재시작 루프에서 반복적으로 호출된다.
def _run_cli_once(port: str) -> None:
    """CLI 모드의 1회 실행: 키오스크 루프 진입."""
    asyncio.run(run_cli_main(port))


# [추가] 예외 발생 시 자동 재시작을 수행하는 감독(supervisor) 루프.
#        - 지수 백오프로 재시작 간격을 늘려가며 무한정 재시도(기본)
#        - KeyboardInterrupt 는 정상 종료로 처리하여 위로 전달
#        - SystemExit 도 의도적 종료로 간주하여 위로 전달
def _run_with_supervisor(port: str) -> int:
    """예외가 발생해도 자동으로 재시작하는 감독 루프."""
    logger = logging.getLogger("supervisor")
    delay = RESTART_INITIAL_DELAY
    consecutive_failures = 0

    while True:
        try:
            _run_cli_once(port)
            # 정상 종료(예: run_kiosk가 자연스럽게 끝남) → 루프 탈출
            logger.info("키오스크 루프가 정상 종료되었습니다.")
            return 0

        except KeyboardInterrupt:
            # 사용자 의도적 종료 — 상위 핸들러로 전달
            raise

        except SystemExit:
            # raise SystemExit(...) 같이 의도적 종료 — 상위로 전달
            raise

        except Exception as e:
            consecutive_failures += 1
            logger.exception(
                "키오스크 실행 중 예외 발생 (연속 실패 %d회): %s",
                consecutive_failures,
                e,
            )

            # 최대 연속 실패 횟수 도달 시 종료(0이면 무제한)
            if (
                MAX_CONSECUTIVE_FAILURES > 0
                and consecutive_failures >= MAX_CONSECUTIVE_FAILURES
            ):
                logger.error(
                    "연속 실패 %d회 도달 — 프로그램을 종료합니다.",
                    consecutive_failures,
                )
                return 1

            logger.warning(
                "%d초 후 재시작합니다... (Ctrl+C 로 중지 가능)", delay
            )
            try:
                time.sleep(delay)
            except KeyboardInterrupt:
                raise

            # 지수 백오프 적용 (상한 RESTART_MAX_DELAY)
            delay = min(delay * RESTART_BACKOFF_FACTOR, RESTART_MAX_DELAY)


def main() -> int:
    setup_logging()
    logger = logging.getLogger("main")

    logger.info(f"{'=' * 50}")
    logger.info(f"설정: {config}")
    logger.info(f"{'=' * 50}")

    # [수정] 시리얼 포트 해석 단계의 SystemExit/예외도 안전하게 잡아 종료 코드 반환.
    try:
        if not config.pcb_control_enabled:
            logger.info(
                "ASSET_DEVICE_TYPE=%s — PCB 제어 비활성화 (시리얼 포트 검색·연결 생략)",
                config.asset_device_type,
            )
            port = ""
        elif config.test_mode_enabled:
            logger.info(
                "TEST_MODE_ENABLED=true — 시리얼 포트 검색 생략 (가짜 시리얼 사용)"
            )
            port = "FAKE"
        else:
            port = resolve_serial_port()
            if port != config.serial_port.strip():
                logger.info(f"시리얼 포트(자동): {port}")
    except SystemExit as e:
        logger.error("초기화 단계 종료: %s", e)
        return 1
    except Exception as e:
        logger.exception("초기화 단계 예외: %s", e)
        return 1

    if not config.pcb_control_enabled:
        logger.info(
            "ASSET_DEVICE_TYPE=%s — PCB 제어 비활성화, 실행할 작업이 없어 종료합니다.",
            config.asset_device_type,
        )
        return 0

    # [수정] 단순 asyncio.run 호출 대신 감독(supervisor) 루프 사용 — 예외 발생 시 자동 재시작.
    return _run_with_supervisor(port)


if __name__ == "__main__":
    # [수정] KeyboardInterrupt 외의 모든 예외에 대한 최외곽 안전망 추가.
    #        이 블록은 마지막 방어선 — 모든 하위 단계에서 누락된 예외만 여기로 옴.
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n프로그램이 종료되었습니다.")
        sys.exit(0)
    except SystemExit:
        # main() 의 반환 코드(또는 의도적 SystemExit)를 그대로 전달
        raise
    except Exception as e:
        # 어떤 단계에서도 잡히지 않은 최종 예외 — 트레이스백 대신 로그로 기록
        logging.getLogger("main").exception("치명적 오류로 종료합니다: %s", e)
        sys.exit(1)
