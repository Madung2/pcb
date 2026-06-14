"""
널 모뎀 테스트 스크립트

USB-RS232 컨버터 2개 + 널 모뎀 케이블로 테스트할 때 사용.

사용법:
    1. 두 컨버터를 미니 PC에 연결 (예: COM3, COM4)
    2. 널 모뎀 젠더로 두 컨버터 연결
    3. 실행: python test_null_modem.py --tx COM3 --rx COM4

테스트 내용:
    - 제어 패킷(Command 'L') 송신 → 수신 확인
    - 상태 요청 패킷(Command 'S') 송신 → 가짜 응답 수신
    - BCC 검증
    - 프레임 파싱 검증
"""

import argparse
import sys
import time
import threading

import serial

from kiosk_module.protocol import (
    FrameBuilder,
    FrameParser,
    LightMode,
    DoorAction,
    SpeakerMode,
    STX,
    ETX,
    CMD_STATUS,
    calc_bcc,
)


def hex_dump(data: bytes, label: str = ""):
    """바이트를 보기 좋게 출력."""
    hex_str = " ".join(f"{b:02X}" for b in data)
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    print(f"  {label:>6} [{len(data):3d}B]: {hex_str}")
    print(f"         ASCII: {ascii_str}")


class NullModemTester:
    """널 모뎀 테스트 클래스."""

    def __init__(self, tx_port: str, rx_port: str, baudrate: int = 115200):
        self.tx_port = tx_port
        self.rx_port = rx_port
        self.baudrate = baudrate

        self.tx_serial = None
        self.rx_serial = None
        self.passed = 0
        self.failed = 0

    def setup(self):
        """시리얼 포트 열기."""
        print(f"\n{'='*60}")
        print(f"  널 모뎀 테스트")
        print(f"  TX: {self.tx_port}  →  RX: {self.rx_port}")
        print(f"  속도: {self.baudrate} BPS")
        print(f"{'='*60}\n")

        try:
            self.tx_serial = serial.Serial(
                self.tx_port, self.baudrate, timeout=1
            )
            self.rx_serial = serial.Serial(
                self.rx_port, self.baudrate, timeout=1
            )
            print(f"  [OK] TX 포트 열림: {self.tx_port}")
            print(f"  [OK] RX 포트 열림: {self.rx_port}")
            time.sleep(0.5)  # 포트 안정화
            return True
        except serial.SerialException as e:
            print(f"  [FAIL] 포트 열기 실패: {e}")
            return False

    def teardown(self):
        """시리얼 포트 닫기."""
        if self.tx_serial:
            self.tx_serial.close()
        if self.rx_serial:
            self.rx_serial.close()

    def _check(self, name: str, condition: bool, detail: str = ""):
        """테스트 결과 기록."""
        if condition:
            self.passed += 1
            print(f"  [PASS] {name}")
        else:
            self.failed += 1
            print(f"  [FAIL] {name} {detail}")

    # ──────────────────────────────────────────
    # 테스트 1: 제어 패킷 송수신
    # ──────────────────────────────────────────
    def test_control_frame(self):
        """Command 'L' 제어 패킷 테스트."""
        print(f"\n--- 테스트 1: 제어 패킷 (Command 'L') ---")

        frame = FrameBuilder.build_control_frame(
            ac_light1=LightMode.ON,
            dc_light1=LightMode.DIMMING,
            dc_light_brightness1=9,
            door=DoorAction.OPEN,
            speaker=SpeakerMode.MAIN,
        )

        hex_dump(frame, "TX")

        # 전송
        self.tx_serial.write(frame)
        self.tx_serial.flush()
        time.sleep(0.1)

        # 수신
        received = self.rx_serial.read(len(frame) + 10)
        hex_dump(received, "RX")

        # 검증
        self._check("길이 일치", len(received) == len(frame),
                     f"(TX={len(frame)}, RX={len(received)})")
        self._check("내용 일치", received == frame)
        self._check("STX 확인", received[0] == STX if received else False)
        self._check("ETX 확인", received[-1] == ETX if received else False)
        self._check("BCC 유효", FrameParser.validate_frame(received) if received else False)

        # 프레임 파싱
        if received:
            cmd = FrameParser.get_command(received)
            self._check("COMMAND='L'", cmd == ord("L"),
                         f"(받은값: {chr(cmd) if cmd else 'N/A'})")

    # ──────────────────────────────────────────
    # 테스트 2: 상태 요청 패킷
    # ──────────────────────────────────────────
    def test_status_request(self):
        """Command 'S' 상태 요청 패킷 테스트."""
        print(f"\n--- 테스트 2: 상태 요청 (Command 'S') ---")

        frame = FrameBuilder.build_status_request_frame()
        hex_dump(frame, "TX")

        self.tx_serial.write(frame)
        self.tx_serial.flush()
        time.sleep(0.1)

        received = self.rx_serial.read(len(frame) + 10)
        hex_dump(received, "RX")

        self._check("내용 일치", received == frame)
        self._check("BCC 유효", FrameParser.validate_frame(received) if received else False)

    # ──────────────────────────────────────────
    # 테스트 3: 가짜 상태 응답 (RX→TX 방향)
    # ──────────────────────────────────────────
    def test_fake_status_response(self):
        """PCB 역할: RX 포트에서 가짜 상태 응답을 보내고 TX에서 수신."""
        print(f"\n--- 테스트 3: 가짜 상태 응답 (PCB 시뮬레이션) ---")

        # 가짜 상태 응답 프레임 수동 조립
        # STX | 'S' | AC상태=1 | DC상태=2 | DC밝기=128 | 스피커=1 | 사람=1 | 버튼=0 | BCC | ETX
        # Command 'S' DATA 11바이트 (명세 순서)
        payload = bytes([
            CMD_STATUS,
            0x01,  # AC1 ON
            0x00,  # AC2 OFF
            0x02,  # DC1 DIMMING
            0x00,  # DC2 OFF
            0x08,  # DC 밝기1 (프로토콜 0~10)
            0x00,  # DC 밝기2
            0x00,  # DOOR
            0x01,  # 스피커 ON
            0x01,  # 사람 감지
            0x00,  # 좌측 버튼
            0x00,  # 우측 버튼
        ])
        bcc = calc_bcc(payload)
        fake_response = bytes([STX]) + payload + bytes([bcc, ETX])

        hex_dump(fake_response, "TX(PCB)")

        # RX 포트(PCB 역할)에서 전송
        self.rx_serial.write(fake_response)
        self.rx_serial.flush()
        time.sleep(0.1)

        # TX 포트(키오스크)에서 수신
        received = self.tx_serial.read(len(fake_response) + 10)
        hex_dump(received, "RX(키오)")

        self._check("프레임 수신", len(received) > 0)
        self._check("BCC 유효", FrameParser.validate_frame(received) if received else False)

        # 파싱 테스트
        if received and FrameParser.validate_frame(received):
            status = FrameParser.parse_status_response(received)
            if status:
                self._check("AC1=1", status.ac_light_status1 == 1)
                self._check("DC1=2", status.dc_light_status1 == 2)
                self._check("DC 밝기1=8", status.dc_light_brightness1 == 8)
                self._check("스피커=1", status.speaker_status == 1)
                self._check("사람감지=1", status.person_detected == 1)
                self._check("좌/우 버튼=0", status.button_left_status == 0 and status.button_right_status == 0)
                print(f"\n  파싱 결과: {status}")
            else:
                self._check("상태 파싱", False, "파싱 실패")

    # ──────────────────────────────────────────
    # 테스트 4: 연속 전송 (깨짐 테스트)
    # ──────────────────────────────────────────
    def test_burst_frames(self, count: int = 20):
        """연속 프레임 전송 테스트."""
        print(f"\n--- 테스트 4: 연속 전송 ({count}회) ---")

        frames = []
        for i in range(count):
            frame = FrameBuilder.build_control_frame(
                ac_light1=LightMode(i % 2),
                dc_light1=LightMode(i % 3),
                dc_light_brightness1=i * 10 % 256,
                door=DoorAction(i % 3),
                speaker=SpeakerMode(i % 2),
            )
            frames.append(frame)

        # 한꺼번에 전송
        for frame in frames:
            self.tx_serial.write(frame)
        self.tx_serial.flush()
        time.sleep(0.5)

        # 수신
        all_data = b""
        while self.rx_serial.in_waiting > 0:
            all_data += self.rx_serial.read(self.rx_serial.in_waiting)
            time.sleep(0.05)

        # 프레임 추출
        extracted, remaining = FrameParser.extract_frames(all_data)

        self._check(
            f"프레임 {count}개 수신",
            len(extracted) == count,
            f"(받은 수: {len(extracted)})",
        )

        valid_count = sum(1 for f in extracted if FrameParser.validate_frame(f))
        self._check(
            f"BCC 모두 유효",
            valid_count == len(extracted),
            f"(유효: {valid_count}/{len(extracted)})",
        )

    # ──────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────
    def run_all(self):
        """모든 테스트 실행."""
        if not self.setup():
            return

        try:
            self.test_control_frame()
            self.test_status_request()
            self.test_fake_status_response()
            self.test_burst_frames()

            print(f"\n{'='*60}")
            print(f"  결과: {self.passed} PASSED / {self.failed} FAILED")
            print(f"{'='*60}\n")

        finally:
            self.teardown()


def main():
    parser = argparse.ArgumentParser(
        description="널 모뎀 테스트 (USB-RS232 컨버터 2개 필요)"
    )
    parser.add_argument(
        "--tx", default="COM3", help="송신용 COM 포트 (기본: COM3)"
    )
    parser.add_argument(
        "--rx", default="COM4", help="수신용 COM 포트 (기본: COM4)"
    )
    parser.add_argument(
        "--baud", type=int, default=115200, help="통신 속도 (기본: 115200)"
    )

    # 사용 가능한 포트 표시
    import serial.tools.list_ports
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if ports:
        print(f"\n  감지된 COM 포트: {', '.join(ports)}")
    else:
        print("\n  [경고] 감지된 COM 포트가 없습니다!")

    args = parser.parse_args()
    tester = NullModemTester(args.tx, args.rx, args.baud)
    tester.run_all()


if __name__ == "__main__":
    main()
