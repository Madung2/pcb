from kiosk_module.device_websocket import build_device_websocket_url


def test_build_device_websocket_url_appends_device_id() -> None:
    assert (
        build_device_websocket_url(
            "wss://hcg.jdone.co.kr/api/ws/device",
            "device-1",
        )
        == "wss://hcg.jdone.co.kr/api/ws/device?device_id=device-1"
    )


def test_build_device_websocket_url_preserves_other_query_params() -> None:
    assert (
        build_device_websocket_url(
            "wss://hcg.jdone.co.kr/api/ws/device?token=abc",
            "device-1",
        )
        == "wss://hcg.jdone.co.kr/api/ws/device?token=abc&device_id=device-1"
    )


def test_build_device_websocket_url_replaces_existing_device_id() -> None:
    assert (
        build_device_websocket_url(
            "wss://hcg.jdone.co.kr/api/ws/device?device_id=old",
            "device-1",
        )
        == "wss://hcg.jdone.co.kr/api/ws/device?device_id=device-1"
    )
