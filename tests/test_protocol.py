"""
프로토콜 유닛 테스트

시리얼 포트 없이 프레임 빌드/파싱/BCC 로직을 검증.
"""

import unittest

from kiosk_module.protocol import (
    STX,
    ETX,
    CMD_CONTROL,
    CMD_STATUS,
    NO_CHANGE,
    FrameBuilder,
    FrameParser,
    LightMode,
    DoorAction,
    DoorStatus,
    SpeakerMode,
    calc_bcc,
)


class TestBCC(unittest.TestCase):
    """BCC 계산 테스트."""

    def test_single_byte(self):
        self.assertEqual(calc_bcc(b"\x4C"), 0x4C)  # 'L'

    def test_xor_basic(self):
        # 0x4C ^ 0x01 = 0x4D
        self.assertEqual(calc_bcc(b"\x4C\x01"), 0x4D)

    def test_xor_cancels(self):
        # A ^ A = 0
        self.assertEqual(calc_bcc(b"\xFF\xFF"), 0x00)

    def test_empty(self):
        self.assertEqual(calc_bcc(b""), 0)


class TestFrameBuilder(unittest.TestCase):
    """프레임 빌더 테스트."""

    def test_control_frame_structure(self):
        """제어 프레임이 STX로 시작, ETX로 끝나는지."""
        frame = FrameBuilder.build_control_frame()
        self.assertEqual(frame[0], STX)
        self.assertEqual(frame[-1], ETX)
        # STX | L | AC1 AC2 DC1 DC2 B1 B2 DOOR SPK | BCC | ETX
        self.assertEqual(len(frame), 12)

    def test_control_frame_command(self):
        """제어 프레임의 COMMAND가 'L'인지."""
        frame = FrameBuilder.build_control_frame()
        self.assertEqual(frame[1], CMD_CONTROL)
        self.assertEqual(frame[1], ord("L"))

    def test_control_frame_no_args_all_nochange(self):
        """인자를 하나도 안 주면 DATA 8바이트 전부 NO_CHANGE(9)."""
        frame = FrameBuilder.build_control_frame()
        # frame[2:10] = DATA 8바이트
        self.assertEqual(list(frame[2:10]), [NO_CHANGE] * 8)

    def test_control_frame_data_values(self):
        """제어 프레임 DATA 필드 값 확인."""
        frame = FrameBuilder.build_control_frame(
            ac_light1=LightMode.ON,
            ac_light2=LightMode.OFF,
            dc_light1=LightMode.DIMMING,
            dc_light2=LightMode.OFF,
            dc_light_brightness1=200,
            dc_light_brightness2=0,
            door=DoorAction.OPEN,
            speaker=SpeakerMode.MAIN,
        )
        # STX CMD AC1 AC2 DC1 DC2 B1 B2 DOOR SPK BCC ETX
        self.assertEqual(frame[2], 1)    # AC1=ON
        self.assertEqual(frame[3], 0)    # AC2=OFF
        self.assertEqual(frame[4], 2)    # DC1=DIMMING
        self.assertEqual(frame[5], 0)    # DC2=OFF
        self.assertEqual(frame[6], 10)   # DC 밝기1 (200 → 최대 10으로 클램프)
        self.assertEqual(frame[7], 0)    # DC 밝기2
        self.assertEqual(frame[8], 1)    # DOOR=OPEN
        self.assertEqual(frame[9], 1)    # SPEAKER=MAIN

    def test_control_frame_partial_fields_others_nochange(self):
        """설정하지 않은 필드는 NO_CHANGE(9)로 채워지는지."""
        frame = FrameBuilder.build_control_frame(ac_light1=LightMode.ON)
        self.assertEqual(frame[2], 1)            # AC1 = ON
        self.assertEqual(frame[3], NO_CHANGE)    # AC2
        self.assertEqual(frame[4], NO_CHANGE)    # DC1
        self.assertEqual(frame[5], NO_CHANGE)    # DC2
        self.assertEqual(frame[6], NO_CHANGE)    # B1
        self.assertEqual(frame[7], NO_CHANGE)    # B2
        self.assertEqual(frame[8], NO_CHANGE)    # DOOR
        self.assertEqual(frame[9], NO_CHANGE)    # SPK

    def test_control_frame_door_only_keeps_lights_nochange(self):
        """DOOR만 지정해도 나머지(조명·스피커)는 9로 나가서 기존 상태 유지."""
        frame = FrameBuilder.build_control_frame(door=DoorAction.OPEN)
        self.assertEqual(frame[2], NO_CHANGE)
        self.assertEqual(frame[3], NO_CHANGE)
        self.assertEqual(frame[4], NO_CHANGE)
        self.assertEqual(frame[5], NO_CHANGE)
        self.assertEqual(frame[6], NO_CHANGE)
        self.assertEqual(frame[7], NO_CHANGE)
        self.assertEqual(frame[8], DoorAction.OPEN)
        self.assertEqual(frame[9], NO_CHANGE)

    def test_control_frame_bcc_valid(self):
        """제어 프레임 BCC가 올바른지."""
        frame = FrameBuilder.build_control_frame(
            ac_light1=LightMode.ON, dc_light1=LightMode.ON
        )
        self.assertTrue(FrameParser.validate_frame(frame))

    def test_control_frame_brightness_clamp(self):
        """밝기값이 0~10 범위로 제한되는지."""
        frame = FrameBuilder.build_control_frame(dc_light_brightness1=999)
        self.assertEqual(frame[6], 10)

        frame = FrameBuilder.build_control_frame(dc_light_brightness1=-10)
        self.assertEqual(frame[6], 0)

        frame = FrameBuilder.build_control_frame(dc_light_brightness2=500)
        self.assertEqual(frame[7], 10)

    def test_status_request_frame(self):
        """상태 요청 프레임 구조 확인."""
        frame = FrameBuilder.build_status_request_frame()
        self.assertEqual(frame[0], STX)
        self.assertEqual(frame[1], CMD_STATUS)  # 'S'
        self.assertEqual(frame[2], 0x00)        # DUMMY
        self.assertEqual(frame[-1], ETX)
        self.assertTrue(FrameParser.validate_frame(frame))

    def test_gps_request_frame(self):
        """GPS 요청 프레임 구조."""
        frame = FrameBuilder.build_gps_request_frame()
        self.assertEqual(frame[0], STX)
        self.assertEqual(frame[1], ord("T"))
        self.assertEqual(frame[-1], ETX)
        self.assertTrue(FrameParser.validate_frame(frame))


class TestFrameParser(unittest.TestCase):
    """프레임 파서 테스트."""

    def _make_status_response(
        self,
        ac1=1,
        ac2=0,
        dc1=2,
        dc2=0,
        b1=8,
        b2=0,
        door=0,
        spk=1,
        person=1,
        btn_l=0,
        btn_r=0,
    ) -> bytes:
        """가짜 상태 응답 프레임 생성 (``S`` + DATA 11바이트)."""
        payload = bytes(
            [
                CMD_STATUS,
                ac1,
                ac2,
                dc1,
                dc2,
                b1,
                b2,
                door,
                spk,
                person,
                btn_l,
                btn_r,
            ]
        )
        bcc = calc_bcc(payload)
        return bytes([STX]) + payload + bytes([bcc, ETX])

    def test_validate_valid_frame(self):
        frame = self._make_status_response()
        self.assertTrue(FrameParser.validate_frame(frame))

    def test_validate_corrupted_bcc(self):
        frame = bytearray(self._make_status_response())
        frame[-2] ^= 0xFF  # BCC 변조
        self.assertFalse(FrameParser.validate_frame(bytes(frame)))

    def test_validate_too_short(self):
        self.assertFalse(FrameParser.validate_frame(b"\x02\x03"))

    def test_validate_wrong_stx(self):
        self.assertFalse(FrameParser.validate_frame(b"\x00\x53\x00\x53\x03"))

    def test_extract_single_frame(self):
        frame = self._make_status_response()
        frames, remaining = FrameParser.extract_frames(frame)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], frame)
        self.assertEqual(remaining, b"")

    def test_extract_multiple_frames(self):
        f1 = FrameBuilder.build_status_request_frame()
        f2 = self._make_status_response()
        buffer = f1 + f2
        frames, remaining = FrameParser.extract_frames(buffer)
        self.assertEqual(len(frames), 2)

    def test_extract_with_garbage_prefix(self):
        """앞에 쓰레기 데이터가 있어도 프레임 추출."""
        frame = self._make_status_response()
        buffer = b"\xFF\xAA\xBB" + frame
        frames, remaining = FrameParser.extract_frames(buffer)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], frame)

    def test_extract_incomplete_frame(self):
        """ETX가 아직 안 온 불완전 프레임."""
        frame = self._make_status_response()
        partial = frame[:-1]  # ETX 잘림
        frames, remaining = FrameParser.extract_frames(partial)
        self.assertEqual(len(frames), 0)
        self.assertTrue(len(remaining) > 0)

    def test_parse_status_response(self):
        """상태 응답 파싱."""
        frame = self._make_status_response(
            ac1=1,
            ac2=0,
            dc1=2,
            dc2=0,
            b1=8,
            b2=0,
            door=0,
            spk=1,
            person=1,
            btn_l=0,
            btn_r=0,
        )
        status = FrameParser.parse_status_response(frame)
        self.assertIsNotNone(status)
        self.assertEqual(status.ac_light_status1, 1)
        self.assertEqual(status.ac_light_status2, 0)
        self.assertEqual(status.dc_light_status1, 2)
        self.assertEqual(status.dc_light_status2, 0)
        self.assertEqual(status.dc_light_brightness1, 8)
        self.assertEqual(status.dc_light_brightness2, 0)
        self.assertEqual(status.door_status, 0)
        self.assertEqual(status.speaker_status, 1)
        self.assertEqual(status.person_detected, 1)
        self.assertEqual(status.button_left_status, 0)
        self.assertEqual(status.button_right_status, 0)

    def test_parse_status_response_door_fault(self):
        """DOOR 상태 3(고장) 수신 시 파싱 성공."""
        frame = self._make_status_response(door=DoorStatus.FAULT)
        status = FrameParser.parse_status_response(frame)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.door_status, DoorStatus.FAULT)

    def test_parse_status_response_invalid_brightness_returns_none(self):
        """밝기가 프로토콜 범위를 벗어나면 검증 실패 → None."""
        frame = self._make_status_response(b1=200)
        self.assertIsNone(FrameParser.parse_status_response(frame))

    def test_parse_status_wrong_command(self):
        """다른 COMMAND의 프레임은 None 반환."""
        frame = FrameBuilder.build_gps_request_frame()
        status = FrameParser.parse_status_response(frame)
        self.assertIsNone(status)

    def test_get_command(self):
        frame = FrameBuilder.build_control_frame()
        self.assertEqual(FrameParser.get_command(frame), ord("L"))

        frame = FrameBuilder.build_status_request_frame()
        self.assertEqual(FrameParser.get_command(frame), ord("S"))


class TestRoundTrip(unittest.TestCase):
    """빌드 → 파싱 왕복 테스트."""

    def test_control_roundtrip(self):
        """제어 프레임 빌드 후 유효성 검증."""
        for ac in LightMode:
            for dc in LightMode:
                for door in DoorAction:
                    for spk in SpeakerMode:
                        frame = FrameBuilder.build_control_frame(
                            ac_light1=ac,
                            dc_light1=dc,
                            dc_light_brightness1=10,
                            door=door,
                            speaker=spk,
                        )
                        self.assertTrue(
                            FrameParser.validate_frame(frame),
                            f"BCC 실패: ac={ac}, dc={dc}, door={door}, spk={spk}",
                        )

    def test_control_roundtrip_no_args(self):
        """인자 없는 제어 프레임(전부 NO_CHANGE)도 BCC가 유효한지."""
        frame = FrameBuilder.build_control_frame()
        self.assertTrue(FrameParser.validate_frame(frame))

    def test_status_request_roundtrip(self):
        frame = FrameBuilder.build_status_request_frame()
        self.assertTrue(FrameParser.validate_frame(frame))
        self.assertEqual(FrameParser.get_command(frame), ord("S"))


if __name__ == "__main__":
    unittest.main()
