"""
기기 제어 클래스 (Command 'L')

백엔드 또는 키오스크 앱에서 호출하여 PCB의 조명, 도어, 스피커를 제어.

PCB 프로토콜상 Command 'L' 의 각 DATA 바이트는 **제어할 모듈만** 0/1 등의 값을
넣고, 그 외 모듈은 ``NO_CHANGE``(9)로 보내서 기존 상태를 유지시킵니다.
``PcbControlInput``에서 설정되지 않은(``None``) 필드는 자동으로 9로 전송됩니다.
"""

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .protocol import (
    DoorAction,
    FrameBuilder,
    LightMode,
    SpeakerMode,
)
from .serial_manager import SerialManager

logger = logging.getLogger(__name__)


class PcbControlInput(BaseModel):
    """PCB Command 'L' 제어 필드 스키마 (하드웨어 고정).

    각 필드는 해당 모듈을 **제어할 때만** 값을 지정합니다. 생략하거나 ``None``으로
    두면 프레임에 ``NO_CHANGE``(9)가 실려 PCB가 해당 모듈의 기존 상태를 유지합니다.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    ac_light1: LightMode | None = None
    ac_light2: LightMode | None = None
    dc_light1: LightMode | None = None
    dc_light2: LightMode | None = None
    dc_light_brightness1: int | None = Field(default=None, ge=0, le=255)
    dc_light_brightness2: int | None = Field(default=None, ge=0, le=255)
    door: DoorAction | None = None
    speaker: SpeakerMode | None = None

    @field_validator(
        "ac_light1",
        "ac_light2",
        "dc_light1",
        "dc_light2",
        mode="before",
    )
    @classmethod
    def _coerce_light_mode(cls, v):
        if v is None or isinstance(v, LightMode):
            return v
        if isinstance(v, str):
            return LightMode[v.upper()]
        return v

    @field_validator("door", mode="before")
    @classmethod
    def _coerce_door(cls, v):
        if v is None or isinstance(v, DoorAction):
            return v
        if isinstance(v, str):
            return DoorAction[v.upper()]
        return v

    @field_validator("speaker", mode="before")
    @classmethod
    def _coerce_speaker(cls, v):
        if v is None or isinstance(v, SpeakerMode):
            return v
        if isinstance(v, str):
            return SpeakerMode[v.upper()]
        return v


class Controller:
    """PCB 기기 제어 클래스.

    조명, 도어, 스피커 등의 하드웨어를 제어하는 고수준 인터페이스.

    Usage:
        serial_mgr = SerialManager(port="COM3")
        serial_mgr.open()

        controller = Controllerer(serial_mgr)

        controller.set_ac_light(on=True)  # 기본 채널 1
        controller.set_ac_light(on=False, channel=2)

        controller.set_dc_light(mode=LightMode.DIMMING, brightness=150, channel=1)
        controller.set_dc_light(mode=LightMode.ON, brightness=80, channel=2)

        controller.open_door()

        controller.send_control(
            PcbControlInput(
                ac_light1=LightMode.ON,
                dc_light1=LightMode.DIMMING,
                dc_light_brightness1=200,
                door=DoorAction.OPEN,
                speaker=SpeakerMode.MAIN,
            )
        )
    """

    def __init__(self, serial_manager: SerialManager):
        self._serial = serial_manager

    def send_control(self, control: PcbControlInput) -> bool:
        """제어 프레임 전송 (Command 'L').

        ``control``에서 실제로 값이 설정된 필드만 PCB에 제어 명령으로 보내고,
        나머지 필드는 프레임에 ``NO_CHANGE``(9)가 실려 PCB가 해당 모듈의 기존
        상태를 유지합니다. JSON 등에서 ``PcbControlInput.model_validate(...)``로
        생성해 넘기면 됩니다.

        Args:
            control: PCB 제어 입력 모델

        Returns:
            True = 전송 성공
        """
        frame = FrameBuilder.build_control_frame(
            ac_light1=control.ac_light1,
            ac_light2=control.ac_light2,
            dc_light1=control.dc_light1,
            dc_light2=control.dc_light2,
            dc_light_brightness1=control.dc_light_brightness1,
            dc_light_brightness2=control.dc_light_brightness2,
            door=control.door,
            speaker=control.speaker,
        )

        def _fmt(name: str, value) -> str:
            if value is None:
                return f"{name}=-"
            if hasattr(value, "name"):
                return f"{name}={value.name}"
            return f"{name}={value}"

        logger.info(
            "제어 전송: "
            + " ".join(
                [
                    _fmt("AC1", control.ac_light1),
                    _fmt("AC2", control.ac_light2),
                    _fmt("DC1", control.dc_light1),
                    _fmt("DC2", control.dc_light2),
                    _fmt("B1", control.dc_light_brightness1),
                    _fmt("B2", control.dc_light_brightness2),
                    _fmt("DOOR", control.door),
                    _fmt("SPK", control.speaker),
                ]
            )
            + " (미설정 필드는 NO_CHANGE=9)"
        )

        return self._serial.send(frame)

    def set_ac_light(
        self, on: bool, *, channel: Literal[1, 2] = 1
    ) -> bool:
        """AC 조명 ON/OFF.

        Args:
            on: True=ON, False=OFF
            channel: 1 → ``ac_light1``, 2 → ``ac_light2``
        """
        mode = LightMode.ON if on else LightMode.OFF
        if channel == 1:
            return self.send_control(PcbControlInput(ac_light1=mode))
        if channel == 2:
            return self.send_control(PcbControlInput(ac_light2=mode))
        
        raise ValueError(f"channel는 1 또는 2여야 합니다: {channel!r}")

    def set_dc_light(
        self,
        mode: LightMode = LightMode.ON,
        brightness: int = 255,
        *,
        channel: Literal[1, 2] = 1,
    ) -> bool:
        """DC 조명·밝기 제어.

        Args:
            mode: OFF/ON/DIMMING
            brightness: 밝기값 0~255 (DIMMING 모드에서 사용)
            channel: 1 → ``dc_light1`` / ``dc_light_brightness1``,
                2 → ``dc_light2`` / ``dc_light_brightness2``
        """
        if channel == 1:
            return self.send_control(
                PcbControlInput(
                    dc_light1=mode, dc_light_brightness1=brightness
                )
            )
        if channel == 2:
            return self.send_control(
                PcbControlInput(
                    dc_light2=mode, dc_light_brightness2=brightness
                )
            )
        raise ValueError(f"channel는 1 또는 2여야 합니다: {channel!r}")

    def open_door(self) -> bool:
        """도어 열기."""
        return self.send_control(PcbControlInput(door=DoorAction.OPEN))

    def close_door(self) -> bool:
        """도어 닫기."""
        return self.send_control(PcbControlInput(door=DoorAction.CLOSE))

    def set_speaker(self, on: bool) -> bool:
        """스피커 ON/OFF.

        Args:
            on: True=MAIN, False=OFF
        """
        return self.send_control(
            PcbControlInput(
                speaker=SpeakerMode.MAIN if on else SpeakerMode.OFF
            )
        )

    def all_off(self) -> bool:
        """모든 기기 OFF."""
        return self.send_control(
            PcbControlInput(
                ac_light1=LightMode.OFF,
                ac_light2=LightMode.OFF,
                dc_light1=LightMode.OFF,
                dc_light2=LightMode.OFF,
                dc_light_brightness1=0,
                dc_light_brightness2=0,
                door=DoorAction.OFF,
                speaker=SpeakerMode.OFF,
            )
        )

    def all_on(self) -> bool:
        """모든 기기 ON."""
        return self.send_control(
            PcbControlInput(
                ac_light1=LightMode.ON,
                ac_light2=LightMode.ON,
                dc_light1=LightMode.ON,
                dc_light2=LightMode.ON,
                dc_light_brightness1=10,
                dc_light_brightness2=10,
                door=DoorAction.OPEN,
                speaker=SpeakerMode.MAIN,
            )
        )

    def __repr__(self):
        return f"Controllerer(serial={self._serial!r})"


# 공개 API·README·main.py 호환용 별칭
Controllerer = Controller
