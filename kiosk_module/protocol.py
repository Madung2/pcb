"""
GPS SMART KIOSK LED CONTROL 프로토콜 정의

프레임 공통:
    STX(0x02) | COMMAND(1B) | DATA(가변) | BCC(1B) | ETX(0x03)

상태 응답 (Command ``S``) DATA 바이트 순서 (각 1바이트, 총 11바이트):
    AC1 | AC2 | DC1 | DC2 | DC밝기1 | DC밝기2 | DOOR | 스피커 | 사람검지 | 좌버튼 | 우버튼

제어 명령 (Command ``L``) DATA 바이트 순서 (각 1바이트):
    AC조명1 | AC조명2 | DC조명1 | DC조명2 | DC밝기1 | DC밝기2 | DOOR | 스피커
    각 필드는 다음 규칙을 따릅니다:
      - 해당 모듈을 제어할 때 : 0/1 (혹은 DC 조명 모드 2, DC 밝기 0~10 등)
      - 해당 모듈을 제어하지 않을 때 : ``9`` (PCB가 무시하여 기존 상태 유지)

즉 전체: STX | 'L' | AC1 | AC2 | DC1 | DC2 | DC밝기1 | DC밝기2 | DOOR | SPK | BCC | ETX

BCC 계산:
    COMMAND부터 BCC 직전까지 모든 바이트를 XOR
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ──────────────────────────────────────────────
# 프로토콜 상수
# ──────────────────────────────────────────────
STX = 0x02
ETX = 0x03

# ``bytes.find``는 int를 받지 않으므로 1바이트 패턴 재사용
_STX_B = bytes((STX,))
_ETX_B = bytes((ETX,))

# Command 'L' DC 밝기 필드 (1바이트, 프로토콜 상 0~10 스텝)
DC_BRIGHTNESS_MIN = 0
DC_BRIGHTNESS_MAX = 10

CMD_CONTROL = ord("L")  # 관제 → PCB: 조명/도어/스피커 장치 제어
CMD_STATUS = ord("S")  # 관제 ↔ PCB: 상태 요청/응답
CMD_GPS_REQ = ord("T")  # 관제 → PCB: GPS 정보 요청 (OPTION)
CMD_GPS_POS = ord("P")  # 관제 → PCB: GPS 위치 요청 (OPTION)

DUMMY_BYTE = 0x00

# Command 'L' DATA 각 필드에서 "해당 모듈 제어 안 함"을 뜻하는 sentinel 값.
# PCB는 이 값을 받으면 해당 모듈의 기존 상태를 그대로 둡니다.
NO_CHANGE = 9

# ──────────────────────────────────────────────
# 조명/장치 제어 값 (Command 'L' DATA 필드)
# ──────────────────────────────────────────────
class LightMode(IntEnum):
    """조명 동작 모드"""
    OFF = 0
    ON = 1
    DIMMING = 2  # DC 조명만 가능


class DoorAction(IntEnum):
    """도어 동작"""
    OFF = 0
    OPEN = 1
    CLOSE = 2


class DoorStatus(IntEnum):
    """상태 응답(Command ``S``)의 DOOR 바이트.

    제어 명령 ``DoorAction``(OFF/OPEN/CLOSE)과 바이트·의미가 다릅니다.
    """

    UNKNOWN = 0  # 미결정(검지 안 됨)
    CLOSE = 1
    OPEN = 2
    FAULT = 3  # 고장


class SpeakerMode(IntEnum):
    """스피커 모드"""
    OFF = 0
    MAIN = 1


# ──────────────────────────────────────────────
# BCC 계산
# ──────────────────────────────────────────────
def calc_bcc(data: bytes) -> int:
    """COMMAND부터 BCC 직전까지의 바이트를 XOR하여 BCC 계산.

    Args:
        data: COMMAND + DATA 바이트열 (STX/ETX 제외)

    Returns:
        XOR 결과값 (0~255)
    """
    bcc = 0
    for b in data:
        bcc ^= b
    return bcc


def _clamp_dc_brightness(value: int) -> int:
    """DC 조명 밝기를 프로토콜 허용 범위로 제한."""
    return max(DC_BRIGHTNESS_MIN, min(DC_BRIGHTNESS_MAX, value))


def _control_byte(value: Optional[int]) -> int:
    """제어 프레임의 단일 바이트 값을 정규화.

    ``None``(= 미제어)은 ``NO_CHANGE``(9)로 변환, 그 외에는 ``int``로 캐스팅합니다.
    ``LightMode`` / ``DoorAction`` / ``SpeakerMode``도 ``IntEnum`` 이므로 그대로 통과합니다.
    """
    if value is None:
        return NO_CHANGE
    return int(value)


def _brightness_byte(value: Optional[int]) -> int:
    """DC 밝기 바이트 정규화. 미제어(``None``)는 ``NO_CHANGE``, 그 외는 0~10으로 클램프."""
    if value is None:
        return NO_CHANGE
    return _clamp_dc_brightness(int(value))


# ──────────────────────────────────────────────
# 프레임 빌더 (송신: 보낼 패킷 조립)
# ──────────────────────────────────────────────
class FrameBuilder:
    """PCB로 보낼 시리얼 프레임을 조립하는 클래스."""

    @staticmethod
    def _assemble_frame(payload: bytes) -> bytes:
        """COMMAND+DATA 바이트열에 BCC를 계산해 STX·ETX로 감싼 전송 프레임을 만든다."""
        bcc = calc_bcc(payload)
        return bytes([STX]) + payload + bytes([bcc, ETX])

    @staticmethod
    def build_control_frame(
        *,
        ac_light1: Optional[int] = None,
        ac_light2: Optional[int] = None,
        dc_light1: Optional[int] = None,
        dc_light2: Optional[int] = None,
        dc_light_brightness1: Optional[int] = None,
        dc_light_brightness2: Optional[int] = None,
        door: Optional[int] = None,
        speaker: Optional[int] = None,
    ) -> bytes:
        """조명/장치 제어 프레임 생성 (Command 'L').

        각 인자는 해당 모듈을 **제어할 때만** 값을 지정하고, 제어하지 않는 모듈은
        ``None``(기본값)으로 두면 프레임에 ``NO_CHANGE``(9)가 실려 PCB가 무시합니다.

        Args:
            ac_light1, ac_light2: ``LightMode`` 또는 정수(0/1). ``None`` = 미제어.
            dc_light1, dc_light2: ``LightMode`` (DIMMING=2 가능) 또는 정수. ``None`` = 미제어.
            dc_light_brightness1, dc_light_brightness2: 0~10 정수 (범위 밖은 클램프).
                ``None`` = 미제어.
            door: ``DoorAction`` 또는 정수. ``None`` = 미제어.
            speaker: ``SpeakerMode`` 또는 정수. ``None`` = 미제어.

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        payload = bytes([
            CMD_CONTROL,
            _control_byte(ac_light1),
            _control_byte(ac_light2),
            _control_byte(dc_light1),
            _control_byte(dc_light2),
            _brightness_byte(dc_light_brightness1),
            _brightness_byte(dc_light_brightness2),
            _control_byte(door),
            _control_byte(speaker),
        ])

        return FrameBuilder._assemble_frame(payload)

    @staticmethod
    def build_status_request_frame() -> bytes:
        """상태 요청 프레임 생성 (Command 'S').

        PCB에 현재 상태를 물어볼 때 사용.
        DUMMY 바이트 0x00 포함.

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        return FrameBuilder._assemble_frame(bytes([CMD_STATUS, DUMMY_BYTE]))

    @staticmethod
    def build_gps_request_frame() -> bytes:
        """GPS 정보 요청 프레임 생성 (Command 'T', OPTION).

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        return FrameBuilder._assemble_frame(
            bytes([CMD_GPS_REQ, DUMMY_BYTE, DUMMY_BYTE])
        )

    @staticmethod
    def build_gps_position_request_frame() -> bytes:
        """GPS 위치 정보 요청 프레임 생성 (Command 'P', OPTION).

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        return FrameBuilder._assemble_frame(bytes([CMD_GPS_POS, DUMMY_BYTE]))


# ──────────────────────────────────────────────
# 응답 데이터 구조체
# ──────────────────────────────────────────────
# Command 'S' DATA 순서(각 1바이트):
#   AC1 동작상태 | AC2 동작상태 | DC1 동작상태 | DC2 동작상태 |
#   DC1 밝기값 | DC2 밝기값 | DOOR 동작상태 | 스피커 동작상태 |
#   PERSON 검지 | 좌버튼 | 우버튼
_Status01 = Annotated[int, Field(ge=0, le=1)]
_StatusLight = Annotated[int, Field(ge=0, le=2)]  # DC 조명은 DIMMING=2 가능
# DOOR 응답은 제어 명령(``DoorAction``)과 바이트 매핑이 다름:
#   0 = 검지 안됨(=미결정), 1 = CLOSE, 2 = OPEN, 3 = 고장 (``DoorStatus``)
_StatusDoor = Annotated[int, Field(ge=0, le=3)]


class StatusResponse(BaseModel):
    """PCB 상태 응답 (Command ``S`` DATA 11바이트).

    값 범위 요약:
      - AC 조명/스피커/PERSON/버튼: 0(OFF/미검지) 또는 1(ON/검지)
      - DC 조명 동작 상태: 0 OFF, 1 ON, 2 DIMMING
      - DOOR 동작 상태: 0 미결정, 1 CLOSE, 2 OPEN, 3 고장 (``DoorStatus``)
        (제어 명령의 ``DoorAction`` (OFF=0/OPEN=1/CLOSE=2) 과 매핑이 다르니
        ``DoorAction(door_status)`` 식으로 변환하면 안 됨.)
      - DC 밝기값: 0~10 (``DC_BRIGHTNESS_MIN``~``DC_BRIGHTNESS_MAX``)
    """

    model_config = ConfigDict(extra="forbid")

    ac_light_status1: _Status01
    ac_light_status2: _Status01
    dc_light_status1: _StatusLight
    dc_light_status2: _StatusLight
    dc_light_brightness1: Annotated[int, Field(ge=DC_BRIGHTNESS_MIN, le=DC_BRIGHTNESS_MAX)]
    dc_light_brightness2: Annotated[int, Field(ge=DC_BRIGHTNESS_MIN, le=DC_BRIGHTNESS_MAX)]
    door_status: _StatusDoor
    speaker_status: _Status01
    person_detected: _Status01
    button_left_status: _Status01
    button_right_status: _Status01


@dataclass(frozen=True)
class ButtonPressEvent:
    """좌/우 버튼 중 하나 이상이 0→눌림 엣지일 때의 스냅샷.

    ``StatusMonitor``는 동일 폴링 주기 안에서 좌·우 엣지를 합쳐 한 번만 콜백합니다.
    """

    left_pressed: bool
    right_pressed: bool
    left_just_pressed: bool
    right_just_pressed: bool


# ──────────────────────────────────────────────
# 프레임 파서 (수신: 받은 패킷 해석)
# ──────────────────────────────────────────────
class FrameParser:
    """PCB에서 수신한 시리얼 프레임을 파싱하는 클래스."""

    @staticmethod
    def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
        """버퍼에서 완전한 프레임들을 추출.

        STX로 시작하고 ETX로 끝나는 프레임을 찾아냄.

        Args:
            buffer: 수신 버퍼

        Returns:
            (추출된 프레임 목록, 아직 처리하지 못한 버퍼 꼬리)
        """
        frames: list[bytes] = []
        start = 0
        n = len(buffer)

        while start < n:
            stx = buffer.find(_STX_B, start)
            if stx == -1:
                return frames, buffer[start:]
            etx = buffer.find(_ETX_B, stx + 1)
            if etx == -1:
                return frames, buffer[stx:]
            frames.append(buffer[stx : etx + 1])
            start = etx + 1

        return frames, buffer[start:]

    @staticmethod
    def validate_frame(frame: bytes) -> bool:
        """프레임의 STX/ETX/BCC 유효성 검증.

        Args:
            frame: STX ~ ETX 포함된 전체 프레임

        Returns:
            True = 유효, False = 불량
        """
        if len(frame) < 4:  # 최소: STX + CMD + BCC + ETX
            return False
        if frame[0] != STX or frame[-1] != ETX:
            return False

        # BCC 검증: COMMAND ~ BCC 직전
        payload = frame[1:-2]  # STX, BCC, ETX 제외
        expected_bcc = calc_bcc(payload)
        actual_bcc = frame[-2]

        return expected_bcc == actual_bcc

    @staticmethod
    def _frame_data(frame: bytes) -> bytes:
        """COMMAND 다음 ~ BCC 직전 (순수 DATA)."""
        return frame[2:-2]

    @staticmethod
    def get_command(frame: bytes) -> int:
        """프레임에서 COMMAND 바이트 추출."""
        if len(frame) < 4:
            raise ValueError("프레임이 너무 짧습니다")
        return frame[1]

    @staticmethod
    def parse_status_response(frame: bytes) -> Optional[StatusResponse]:
        """상태 응답 프레임 파싱 (Command 'S' 응답).

        Args:
            frame: 수신된 전체 프레임 (STX ~ ETX)

        Returns:
            StatusResponse 또는 None (파싱 실패 시)
        """
        if not FrameParser.validate_frame(frame):
            return None

        cmd = frame[1]
        if cmd != CMD_STATUS:
            return None

        data = FrameParser._frame_data(frame)
        if len(data) < 11:
            return None

        try:
            return StatusResponse(
                ac_light_status1=data[0],
                ac_light_status2=data[1],
                dc_light_status1=data[2],
                dc_light_status2=data[3],
                dc_light_brightness1=data[4],
                dc_light_brightness2=data[5],
                door_status=data[6],
                speaker_status=data[7],
                person_detected=data[8],
                button_left_status=data[9],
                button_right_status=data[10],
            )
        except ValidationError:
            return None

