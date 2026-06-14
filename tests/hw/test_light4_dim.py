"""조명 4 (DC2) 디밍 - 실제 PCB 제어 테스트.

밝기값은 ``--dim-level`` pytest 옵션 또는 ``DIM_LEVEL`` 환경변수로 지정.
둘 다 없으면 기본 5. 우선순위: CLI 옵션 > 환경변수 > 기본값.

실행:
    uv run pytest tests/hw/test_light4_dim.py -v -s
    # CLI 옵션으로 밝기 지정 (권장)
    uv run pytest tests/hw/test_light4_dim.py -v -s --dim-level=3
    # 환경변수 방식 (PowerShell)
    $env:DIM_LEVEL=3; uv run pytest tests/hw/test_light4_dim.py -v -s
"""

from kiosk_module.protocol import LightMode


def test_light4_dim(controller, dim_level):
    ok = controller.set_dc_light(
        mode=LightMode.DIMMING, brightness=dim_level, channel=2
    )
    assert ok, f"조명4(DC2) DIMMING(lv={dim_level}) 전송 실패"
    print(f"[HW] 조명4(DC2) DIMMING 전송 완료 (밝기 {dim_level})")
