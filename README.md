# GPS SMART KIOSK 통신 모듈 (`kiosk_module`)

키오스크 PC와 **PCB(하드웨어)** 사이를 **시리얼(RS232)** 로 연결하고, 필요 시 **백엔드 WebSocket** 과 연동할 수 있는 Python 패키지입니다. 조명·도어·스피커 제어와 PCB 상태 조회를 담당합니다.

---

## 제공 기능


| 영역                | 설명                                                          |
| ----------------- | ----------------------------------------------------------- |
| **시리얼 통신**        | PCB와 포트 연결, 프레임 송수신 (`SerialManager`)                       |
| **기기 제어**         | Command `L`: AC/DC 조명, 도어, 스피커 (`Controllerer`)         |
| **상태 모니터링**       | Command `S`: 주기 폴링 또는 1회 조회, 변화 시 콜백 (`StatusMonitor`)      |
| **프로토콜**          | STX/ETX 프레임, BCC(XOR), 제어·상태·GPS(옵션) 프레임 조립·파싱 (`protocol`) |
| **WebSocket 브릿지** | 백엔드와 WS 연결, 재연결, JSON 송수신 뼈대 (`WSBridge`)                   |
| **통합 WebView**     | Windows에서 내장 `pywebview` UI, 웹뷰 전용 WS, 스크린샷, 트레이, 오류 복구 |
| **설정**            | `.env` 기반 포트, WS URL, 폴링 주기 등 (`config`)                    |


`main.py`는 **시리얼 연결 → 상태 폴링**을 기본으로 돌리는 **실행 예시**입니다. PCB 제어는 앱 코드에서 `Controllerer`로 시리얼만 쓰면 되고, WebSocket URL 이 있으면 백엔드 원격 제어 브릿지도 함께 붙습니다. 그때 수신한 **`type: "control"`** JSON은 `PcbControlInput` 검증 후 `send_control`로 PCB에 반영됩니다. (형식은 [백엔드 → 키오스크 제어 메시지](#백엔드--키오스크-제어-메시지-json) 참고.)

> **제어 필드 미설정 시 동작:** Command `L` 제어 프레임은 "제어하는 모듈만" 0/1 등의 값을 싣고, **설정하지 않은(=`None`) 필드는 자동으로 `NO_CHANGE`(값 `9`)** 로 전송됩니다. PCB는 `9`를 받은 모듈의 기존 상태를 유지합니다. 따라서 이 모듈은 더 이상 "PCB 상태를 캐시해두고 합쳐 보내는" 방식이 아닙니다.

---

## 요구 사항

- **Python 3.13 이상** (필수)
- **[uv](https://docs.astral.sh/uv/)** — 패키지·가상환경 관리
- OS: Windows(일반적으로 `COMn` 포트), macOS/Linux(`tty.*` 등)

### 설치·동기화

[uv 설치](https://docs.astral.sh/uv/getting-started/installation/) 후 프로젝트 루트에서:

```bash
uv sync
```

테스트까지 쓰려면 개발 그룹 포함:

```bash
uv sync --group dev
```

의존성은 `pyproject.toml`에 정의되어 있으며, 잠금 파일은 `uv.lock`입니다.  
주요 패키지: `pyserial`, `websockets`, `python-dotenv`, `pydantic`, `pywebview`, `pillow`, `pystray`.

---

## 설정 (`.env`)

프로젝트 루트에 `.env`를 두면 `kiosk_module.config`가 읽습니다.


| 변수                      | 기본값                      | 설명                         |
| ----------------------- | ------------------------ | -------------------------- |
| `SERIAL_PORT`           | `COM3`                   | 시리얼 포트 이름                  |
| `WS_RECONNECT_INTERVAL` | `5.0`                    | 끊김 후 재연결 대기(초)             |
| `WEBVIEW_ENABLED`       | `false`                  | Windows 통합 WebView 모드 활성화 |
| `DEVICE_ID`             | *(비움)*                 | 백엔드 장치 식별자. API/WebSocket 모두 `device_id`로 전송 |
| `WEBSOCKET_ADDR`        | *(비움)*                 | 백엔드·웹뷰 공통 WS URL. `device_id` 쿼리는 실행 시 자동 추가 |
| `WEBVIEW_DEVTOOLS`      | `false`                  | `pywebview` 개발자 도구 노출 |
| `LOG_LEVEL`             | `INFO`                   | 로그 레벨 (`DEBUG`, `INFO`, …) |


---

## 실행

```bash
uv run python main.py
```

- 시리얼 연결에 실패하면 프로세스가 종료됩니다.
- **Ctrl+C**로 종료하면 (켜져 있으면) WS 종료, 시리얼 닫기 순으로 정리합니다.
- `WEBVIEW_ENABLED=true` 이면 같은 `main.py`가 Windows 전용 통합 WebView 모드로 실행됩니다.
- 이 모드에서는 PCB 루프가 백그라운드 워커로 돌고, 오른쪽 버튼 이벤트가 외부 Chrome 대신 내장 웹뷰를 엽니다.

### 단일 실행 파일 빌드 (예시)

```bash
uv add --group dev pyinstaller
uv run pyinstaller --onefile main.py
```

일회성으로만 쓰려면 `uv tool run pyinstaller --onefile main.py` 도 가능합니다.

### GUI(.exe) 빌드 — `.env` 편집

`gui_main.py` 를 PyInstaller 로 단일 `.exe` 로 묶고 `default.env` 를 함께 번들합니다.
**반드시 Windows 에서 빌드해야 Windows .exe 가 나옵니다.**

```bat
:: Windows 에서
build_exe.bat
```

또는 수동으로:

```bash
uv sync --group gui
uv add --group dev pyinstaller
uv run pyinstaller kiosk_gui.spec --clean --noconfirm
```

산출물: `dist/JDoneKiosk.exe`

**런타임 동작:**
- `.exe` **옆 디렉터리** 가 사용자 데이터 루트.
- **첫 실행 시** 번들된 `default.env` (= 현재 `.env`) 가 `.exe 옆/.env` 로 1회 복사됩니다.
  이미 `.env` 가 있으면 그대로 둡니다 — 업데이트 후에도 사용자 설정 유지.
- GUI 의 `[.env 저장]` 버튼이 화면 값을 위 사용자 `.env` 에 dump 합니다.
  알지 못하는 키는 보존(merge).

`gui_main.py` 화면:
- 시리얼 포트 선택 + 고정 USB 키워드 필터
- `.env` 의 주요 변수 인라인 편집 (`DEVICE_ID`, `WEBSOCKET_ADDR`, `LOG_LEVEL` 등)
- 볼륨 노브용 시리얼 포트 편집 (키오스크 모드에서 항상 사용)
- `[연결]` — PCB 통신 시작, `[끊기]` — 정상 종료
- `[.env 저장]` — 위 값들을 `.env` 파일로 영구 저장

### Watchdog(.exe) 빌드 — 키오스크/보조 프로그램 자동 재실행

`watchdog_gui.py` 를 PyInstaller 로 단일 `.exe` 로 묶습니다.
Watchdog는 `watchdog_config.json` 에 등록된 프로그램을 주기적으로 확인하고,
프로그램이 종료되면 지정한 대기 시간 뒤 다시 실행합니다.

```bat
:: Windows 에서
build_watchdog.bat
```

또는 수동으로:

```bash
uv sync --group gui
uv add --group dev pyinstaller
uv run pyinstaller watchdog_gui.spec --clean --noconfirm
```

산출물: `dist/Watchdog.exe`

**사용 흐름:**
- `Watchdog.exe` 실행
- `[등록]` 버튼으로 `JDoneKiosk.exe` 와 함께 감시할 보조 프로그램 2~3개를 추가
- `[저장]` 후 `[감시 시작]`
- 부팅 후 자동 감시가 필요하면 `Windows 로그인 시 Watchdog 자동 실행`,
  `Watchdog 시작 시 감시 자동 시작`, `자동 실행 시 트레이로 시작` 체크

**런타임 파일:**
- `watchdog_config.json` — 등록 프로그램/옵션 저장
- `watchdog.log` — 시작, 종료 감지, 재시작 실패/성공 로그
- 개발 모드에서는 프로젝트 루트, exe 모드에서는 `Watchdog.exe` 옆에 생성됩니다.

### 테스트

```bash
uv sync --group dev
uv run pytest
```

unittest만 쓸 경우:

```bash
uv run python -m unittest tests.test_protocol -v
```

---

## 패키지 구조

```
pyproject.toml         # 프로젝트 메타·의존성 (requires-python >= 3.13)
uv.lock                # 잠금 파일 (uv)
.python-version        # 로컬 기본 Python (3.13)
kiosk_module/
  __init__.py          # 공개 API re-export
  protocol.py          # 명령·프레임·파서·응답 dataclass
  serial_manager.py    # 시리얼 열기/닫기, 송수신
  device_controller.py # 제어 + PcbControlInput
  status_monitor.py    # 상태 폴링·콜백
  ws_bridge.py         # WebSocket 연결·송수신
  config.py            # 환경변수 설정
main.py                # 데모 엔트리포인트
```

---

## 시리얼 프로토콜 요약

프레임 공통 형식: **STX(0x02) | COMMAND | DATA… | BCC | ETX(0x03)**  
BCC는 COMMAND부터 BCC 직전까지 바이트를 XOR합니다.


| 명령     | 코드         | 용도                    |
| ------ | ---------- | --------------------- |
| 제어     | `L` (0x4C) | 아래 표 참고 (조명·도어·스피커) |
| 상태     | `S` (0x53) | PCB 상태 요청/응답          |
<!-- | GPS 정보 | `T` (옵션)   | GPS 관련 요청             |
| GPS 위치 | `P` (옵션)   | 위치 요청                 | -->


### 제어 명령 (Command `L`) 바이트 순서

송신 프레임은 **총 12바이트**입니다. DATA 각 필드는 **해당 모듈을 제어할 때만** 값(0/1 등)을 싣고, **제어하지 않는 모듈은 `9`(`NO_CHANGE`)** 를 실어 보내면 PCB가 해당 모듈의 기존 상태를 유지합니다.

| 순서 | 필드 | 설명 |
| --- | --- | --- |
| 1 | STX | 시작 (0x02) |
| 2 | COMMAND | 문자 `L` (0x4C) |
| 3 | AC 조명1 | 1바이트 (`LightMode`, 미제어=`9`) |
| 4 | AC 조명2 | 1바이트 (미제어=`9`) |
| 5 | DC 조명1 | 1바이트 (`LightMode`, 미제어=`9`) |
| 6 | DC 조명2 | 1바이트 (미제어=`9`) |
| 7 | DC 조명1 밝기 | 1바이트 (0~10, 미제어=`9`) |
| 8 | DC 조명2 밝기 | 1바이트 (0~10, 미제어=`9`) |
| 9 | DOOR 동작 | 1바이트 (`DoorAction`, 미제어=`9`) |
| 10 | 스피커 전환 | 1바이트 (`SpeakerMode`, 미제어=`9`) |
| 11 | BCC | 체크섬 |
| 12 | ETX | 종료 (0x03) |

열거형 값은 `protocol` 모듈의 `LightMode`, `DoorAction`, `SpeakerMode`를 참고하세요. `NO_CHANGE = 9` 상수도 같은 모듈에 정의되어 있습니다.  
코드에서는 `PcbControlInput`(Pydantic, `None` = 미제어) 필드명이 위 순서와 대응하며, `FrameBuilder.build_control_frame(...)`이 미설정 필드를 자동으로 `9`로 채워 송신합니다.

---

## 기기 제어 (`Controllerer`)

### PcbControlInput (Pydantic)

PCB 제어 필드는 **PcbControlInput** 한 모델로 고정되어 있습니다. 정의되지 않은 키는 허용하지 않습니다(`extra="forbid"`).


| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `ac_light1` | `LightMode` | AC 조명1 (OFF/ON) |
| `ac_light2` | `LightMode` | AC 조명2 |
| `dc_light1` | `LightMode` | DC 조명1 (OFF/ON/DIMMING) |
| `dc_light2` | `LightMode` | DC 조명2 |
| `dc_light_brightness1` | `int` (0~10) | DC 조명1 밝기 |
| `dc_light_brightness2` | `int` (0~10) | DC 조명2 밝기 |
| `door` | `DoorAction` | OFF / OPEN / CLOSE |
| `speaker` | `SpeakerMode` | OFF / MAIN |

`send_control`에 넘긴 모델에서 **실제로 설정된 필드만** 해당 모듈의 제어 값으로 송신되고, 설정하지 않은(=`None`) 필드는 프레임에 `NO_CHANGE`(9)가 실려 PCB가 해당 모듈의 상태를 건드리지 않습니다. 별도로 PCB 상태를 읽어 합쳐 보내는 단계는 없습니다.

- `set_ac_light(on: bool, *, channel=1|2)` — AC 조명 1 또는 2 (`channel` 기본값 1)
- `set_dc_light(mode=..., brightness=..., *, channel=1|2)` — DC 조명·밝기 1 또는 2
- `open_door()` / `close_door()`
- `set_speaker(on: bool)`
- `all_off()` / `all_on()`

현재 PCB 상태는 `StatusMonitor.last_status` / `StatusMonitor.to_dict()`로 확인하세요.

---

## 상태 모니터 (`StatusMonitor`)

- `poll_once()`: 동기로 1회 상태 요청 후 `StatusResponse` 또는 `None`.
- `start_polling(interval)` / `stop_polling()`: 비동기 주기 폴링.

### 콜백


| 콜백                   | 시점                       |
| -------------------- | ------------------------ |
| `on_status_received` | 상태 응답을 받을 때마다            |
| `on_status_changed`  | 이전 대비 상태 필드가 바뀐 경우       |
| `on_person_detected` | 사람 감지 값이 바뀐 경우           |
| `on_button_pressed`  | 좌·우 중 하나라도 0→눌림 엣지일 때 `ButtonPressEvent` 1회 |


JSON으로 넘기기 좋은 형태는 `monitor.to_dict()` (마지막 상태 없으면 `None`).

---

## WebSocket 브릿지 (`WSBridge`)

- `connect()`: 연결 후 수신 루프 (끊기면 `WS_RECONNECT_INTERVAL` 만큼 대기 후 재시도).
- `disconnect()`: 연결 종료.
- `send(data: dict)`: JSON 직렬화 후 전송.
- `send_status()`: `monitor.to_dict()`를 `"type": "status"` 형태로 전송 (필요 시 수정 가능).

수신 처리는 `on_message: Callable[[dict], None]` 에 핸들러를 등록합니다. WebSocket URL 이 있을 때 연결하고, 아래 규약의 제어 메시지를 처리합니다.

### 백엔드 → 키오스크 제어 메시지 (JSON)

백엔드가 키오스크 모듈로 **장비 제어**를 보낼 때는 최상위에 `"type": "control"` 을 두고, `PcbControlInput`과 동일한 필드명을 사용합니다. 문자열 값은 `LightMode` / `DoorAction` / `SpeakerMode`의 **이름**과 같아야 합니다 (`OFF`, `ON`, `DIMMING`, `OPEN`, `CLOSE`, `MAIN` 등).

| 필드 | 예시 값 | 설명 |
| --- | --- | --- |
| `ac_light1`, `ac_light2` | `"ON"`, `"OFF"` | AC 조명 |
| `dc_light1`, `dc_light2` | `"ON"`, `"OFF"`, `"DIMMING"` | DC 조명 모드 |
| `dc_light_brightness1`, `dc_light_brightness2` | `0`~`255` (정수) | DC 밝기 (프로토콜 전송 시 하드웨어 범위로 클램프됨) |
| `door` | `"OPEN"`, `"CLOSE"`, `"OFF"` | 도어 |
| `speaker` | `"MAIN"`, `"OFF"` | 스피커 |

**부분 제어:** 메시지에 **실제로 넣은 필드만** 해당 모듈 제어 명령으로 전송되고, 빠진 필드는 프레임에 `9`(`NO_CHANGE`)가 실려 PCB가 해당 모듈의 기존 상태를 유지합니다 (`send_control` 동작과 동일).

예시 (한 번에 하나만내도 되고, 필요하면 여러 필드를 한 객체에 넣어도 됩니다).

```json
{"type": "control", "ac_light1": "ON"}
```

```json
{"type": "control", "dc_light1": "ON", "dc_light_brightness1": 10}
```

```json
{"type": "control", "dc_light2": "ON", "dc_light_brightness2": 10}
```

```json
{"type": "control", "door": "OPEN"}
```

```json
{"type": "control", "speaker": "MAIN"}
```

`type`이 `"control"`이 아니거나, 제어 필드가 하나도 없으면 핸들러는 무시합니다. 필드명·값이 스키마와 맞지 않으면 로그에 검증 오류가 남고 전송하지 않습니다.

---

## 공개 API (`from kiosk_module import …`)

`kiosk_module/__init__.py`에서 다음 심볼을 제공합니다.

- `FrameBuilder`, `FrameParser`, `calc_bcc`, `NO_CHANGE`
- `SerialManager`
- `Controllerer`, `PcbControlInput`
- `StatusMonitor`
- `WSBridge`

---

## 문제 해결

- **시리얼 열기 실패:** 포트 이름(`SERIAL_PORT`)과 다른 프로그램의 포트 점유 여부를 확인하세요.
- **상태 응답 없음:** 케이블, 보드레이트, PCB 전원 및 프로토콜 일치 여부를 확인하세요.
- **WebSocket만 필요 없음:** `SerialManager` + `Controllerer` / `StatusMonitor`만 임포트해 별도 앱에서 사용할 수 있습니다.



## 지금 구조의 문제점


## 단위 테스트 명령어
[DOOR]
uv run pytest tests/hw/test_door_open.py -v -s
uv run pytest tests/hw/test_door_close.py -v -s

[LIGHT1]
uv run pytest tests/hw/test_light1_on.py -v -s
uv run pytest tests/hw/test_light1_off.py -v -s


[LIGHT2]
uv run pytest tests/hw/test_light2_on.py -v -s
uv run pytest tests/hw/test_light2_off.py -v -s


[LIGHT3]
uv run pytest tests/hw/test_light3_on.py -v -s
uv run pytest tests/hw/test_light3_dim.py -v -s --dim-level=3
uv run pytest tests/hw/test_light3_off.py -v -s


[LIGHT4]
uv run pytest tests/hw/test_light4_on.py -v -s
uv run pytest tests/hw/test_light4_dim.py -v -s --dim-level=3
uv run pytest tests/hw/test_light4_off.py -v -s

[SPEAKER]
uv run pytest tests/hw/test_speaker_on.py -v -s
uv run pytest tests/hw/test_speaker_off.py -v -s
