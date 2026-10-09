# Ssorry — ROS2 + FastAPI 웹 서버

macOS(Apple Silicon)에는 rclpy 를 네이티브로 설치할 수 없어서, ROS2 Humble 컨테이너
(`ros:humble-ros-base`, Ubuntu 22.04 / Python 3.10) 안에서 FastAPI 를 돌린다.
PyCharm 은 이 컨테이너를 원격 인터프리터로 붙여서 rclpy 자동완성과 디버깅을 쓴다.

## 구조

```
Dockerfile           ros:humble + fastapi/uvicorn
docker-compose.yml   포트 8000, app/ 를 마운트해 --reload
requirements.txt     fastapi, uvicorn, pydantic, pillow(MOCK 프레임용)
app/main.py          FastAPI 엔드포인트
app/ros_node.py      rclpy 노드 + spin 스레드 관리
app/stream.py        ROS 스레드 → asyncio 로 카메라 프레임 전달
app/sessions.py      로그인 없는 쿠키 세션
app/judge.py         판정 / 모션 이벤트, 누적 통계, 이력, 구독자별 큐
app/mock.py          MOCK=1 일 때 카메라 / 판정 / 모션을 흉내 내는 노드
app/static/          카메라 뷰어 페이지
```

rclpy 의 executor 는 블로킹이라 asyncio 루프를 막는다. 그래서 `RosBridge` 가
별도 스레드에서 spin 하고, FastAPI 핸들러는 노드 객체의 메서드만 호출한다.
노드의 생명주기는 FastAPI lifespan 에 묶여 있다.

## 실행

공유 네트워크는 최초 한 번만 만들면 된다.

```bash
docker network create ssorry-rosnet   # 최초 1회
docker compose up -d
docker compose logs -f api
```

로봇과 카메라 없이 프론트를 개발할 때는 `MOCK=1` 로 띄운다.
카메라 프레임(10fps, 640x480), 4초마다 판정, 판정 1.5초 뒤 모션 이벤트가 발행된다.

```bash
MOCK=1 docker compose up -d
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `MOCK` | `0` | `1` 이면 더미 퍼블리셔를 함께 띄운다 |
| `CORS_ORIGINS` | (비어 있음) | 쉼표로 구분한 허용 출처. 비어 있으면 CORS 를 걸지 않는다 |
| `CAMERA_TOPIC` | `/camera/image_raw/compressed` | `sensor_msgs/CompressedImage` |
| `JUDGE_TOPIC` | `/ssorry/judge` | `std_msgs/String` 에 판정 JSON |
| `MOTION_TOPIC` | `/ssorry/motion` | `std_msgs/String` 에 모션 JSON |
| `FRONTEND_DIST` | `./frontend-dist` | (compose) 프론트 빌드 결과 폴더. 컨테이너의 `/workspace/frontend` 에 마운트된다 |

### 프론트 서빙

세션은 `SameSite=Lax` 쿠키라서 프론트를 API 와 **같은 출처**에서 열어야 웹소켓에 쿠키가 실린다.
다른 기기에서 열 때도 프론트를 따로 띄우지 말고 이 서버가 서빙한 `http://<서버 IP>:8000/` 로 연다.

- 프론트 빌드 결과(`index.html` 이 있는 폴더)를 `frontend-dist/` 에 두거나 `FRONTEND_DIST` 로 경로를 준다.
- `index.html` 이 있으면 `/` 에서 서빙하고, API 에 없는 경로는 `index.html` 로 돌린다(SPA 라우팅).
- 폴더가 비어 있으면 내장 카메라 뷰어(`app/static/index.html`)가 뜬다.
- 폴더를 처음 채웠거나 비웠을 때는 서버를 재시작해야 반영된다(`docker compose restart api`). 이미 서빙 중일 때 파일만 바꾸는 건 바로 반영된다.
- 개발 중에 Vite 같은 dev 서버를 쓸 때는 dev 서버의 프록시로 `/ws`, `/stats`, `/history`, `/session` 을 `localhost:8000` 에 넘기면 같은 출처가 된다.

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 프론트 빌드 결과, 없으면 내장 카메라 뷰어 (세션 쿠키 발급) |
| `GET /session` | 현재 세션 ID, 열린 웹소켓 수 |
| `WS /ws/camera` | 카메라 JPEG 프레임을 바이너리로 계속 전송 |
| `WS /ws/judge` | 연결 직후 `snapshot`, 이후 `judge` / `stats` / `motion` 이벤트를 JSON 텍스트로 전송 |
| `GET /stats?limit=50` | 누적 통계, 사이클 타임, 최근 판정 `limit` 개 |
| `GET /history` | 판정 이력 전체 |
| `POST /stats/reset` | 누적 통계와 이력 초기화 (데모 재시작용) |
| `GET /health` | 노드 기동 확인 |
| `POST /publish` | `{"text": "..."}` 를 `/chatter` 토픽으로 발행 |
| `GET /last` | `/chatter` 에서 마지막으로 수신한 값 |
| `GET /topics` | 현재 보이는 토픽 목록 |
| `GET /docs` | Swagger UI |

### 카메라 스트리밍

`sensor_msgs/CompressedImage` (JPEG) 토픽을 구독해 `/ws/camera` 로 내보낸다.
토픽은 환경변수 `CAMERA_TOPIC` 으로 바꾸며 기본값은 `/camera/image_raw/compressed`.
raw `Image` 만 내는 카메라라면 image_transport 로 compressed 토픽을 만들어 붙인다.

- 웹소켓에 처음 붙으면 `{"type":"hello","session":...,"topic":...}` 텍스트가 오고, 그 뒤로는 프레임마다 JPEG 바이너리가 온다.
- 클라이언트마다 최신 프레임만 보내므로 느린 클라이언트는 중간 프레임을 건너뛴다.
- 세션은 `ssorry_sid` 쿠키로 구분하며 메모리에만 있다. 쿠키 없이 웹소켓에 붙으면 4401 로 닫힌다.
  연결 없이 1시간 지난 세션은 정리된다.

### 판정 / 모션 이벤트

비전 노드와 모션 노드는 `std_msgs/String` 에 JSON 을 담아 `JUDGE_TOPIC`, `MOTION_TOPIC` 으로 낸다.
판정 이벤트는 하나도 빠지면 안 되므로 웹소켓 클라이언트마다 큐를 둔다
(카메라는 최신 프레임만 보낸다). 통계와 이력은 메모리에만 있어서 재시작하면 초기화된다.

**ROS 토픽으로 들어오는 형식**

```json
// JUDGE_TOPIC — id 는 서버가 붙인다. ts 를 빼면 수신 시각, cycle_time 을 빼면 직전 판정과의 간격을 쓴다.
{"grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160, "bbox": [412, 188, 96, 96], "ts": 1791527966.54}

// MOTION_TOPIC — id 를 넣으면 그 판정에, 빼면 모션이 아직 없는 가장 최근 판정에 roll_detected 가 기록된다.
{"approach_speed": 0.8, "place_height": 0.12, "roll_detected": false, "ts": 1791527968.04}
```

**`WS /ws/judge` 로 나가는 메시지**

세션 처리는 `/ws/camera` 와 같다(쿠키가 없으면 4401). 큐가 1000개 넘게 밀린 클라이언트는
이벤트를 버리는 대신 4408 로 끊는다. 재연결하면 `snapshot` 부터 다시 받는다.

```json
// 연결 직후 한 번, 그리고 POST /stats/reset 직후 모든 클라이언트에 한 번
{"type": "snapshot",
 "stats": {"상": 3, "중": 2, "total": 5},
 "cycle_time": 4.2,
 "recent": [{"id": 5, "grade": "중", "confidence": 0.81, "v_value": 140, "threshold": 160,
             "ts": 1791527982.74, "roll_detected": null}]}

// 판정마다. bbox 는 /ws/camera JPEG 기준 픽셀 [x, y, w, h]
{"type": "judge", "id": 6, "grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160,
 "bbox": [412, 188, 96, 96], "ts": 1791527986.84}

// 판정 직후 갱신된 누적 통계
{"type": "stats", "stats": {"상": 4, "중": 2, "total": 6}, "cycle_time": 4.1}

// 모션 이벤트
{"type": "motion", "approach_speed": 0.8, "place_height": 0.12, "roll_detected": false, "ts": 1791527988.34}
```

**REST 응답**

```json
// GET /stats?limit=50
{"stats": {"상": 4, "중": 2, "total": 6}, "cycle_time": 4.1,
 "recent": [{"id": 6, "grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160,
             "ts": 1791527986.84, "roll_detected": false}]}

// GET /history
[{"id": 1, "grade": "상", "confidence": 0.93, "v_value": 229, "threshold": 160,
  "ts": 1791527966.54, "roll_detected": true}]

// POST /stats/reset
{"stats": {"상": 0, "중": 0, "total": 0}}
```

`roll_detected` 는 그 판정에 대한 모션 이벤트가 오기 전까지 `null` 이다.
`cycle_time` 은 판정이 두 번 이상 들어오기 전까지 `null` 이다.

컨테이너 안에서 ROS2 CLI 를 쓰려면:

```bash
docker compose exec api bash
ros2 topic echo /chatter
```

## 개발 환경 — 실행과 코드 해석의 분리

macOS 에는 rclpy 를 설치할 수 없다. 그래서 둘을 나눴다.

| | 용도 | 내용 |
|---|---|---|
| 컨테이너 | **실제 실행** | ROS2 Humble + Python 3.10.12 |
| `.venv` | PyCharm 코드 해석 | Python 3.10.21 (Homebrew) + fastapi/uvicorn/pydantic |
| `.ros2-stubs/` | PyCharm 코드 해석 | 컨테이너에서 꺼낸 ROS2 파이썬 소스 (rclpy, std_msgs …) |

`.venv` 로는 서버를 실행하지 않는다. rclpy 의 C 확장(`.so`)은 리눅스 바이너리라
로컬에서 import 되지 않는다. 에디터의 자동완성과 타입 해석 전용이다.

PyCharm 인터프리터는 `Python 3.10 (Ssorry)` 라는 이름으로 등록되어 있다.
`.ros2-stubs/` 는 `.venv/lib/python3.10/site-packages/ros2-stubs.pth` 로 경로에 넣는다.
PyCharm 의 Interpreter Paths 에 직접 추가하면 인터프리터를 다시 스캔할 때 빠질 수 있어서
`.pth` 로 고정했다. `.venv` 를 새로 만들었다면 다시 넣어야 한다.
또 PyCharm 이 이 폴더를 인덱싱하도록 `.ros2-stubs` 를 Sources Root 로 지정해 둔다
(Excluded 로 두면 `std_msgs.msg.String` 같은 심볼을 찾지 못한다).

```bash
echo "$PWD/.ros2-stubs" > .venv/lib/python3.10/site-packages/ros2-stubs.pth
```

### 스텁 갱신

ROS2 패키지를 추가로 설치했다면 스텁을 다시 뽑는다. rclpy 는 `site-packages` 가
아니라 `local/lib/python3.10/dist-packages` 에 있으니 두 경로를 모두 가져와야 한다.

```bash
rm -rf .ros2-stubs && mkdir -p .ros2-stubs
docker cp ssorry-api:/opt/ros/humble/lib/python3.10/site-packages/. .ros2-stubs/
docker cp ssorry-api:/opt/ros/humble/local/lib/python3.10/dist-packages/. .ros2-stubs/
find .ros2-stubs -name "*.so*" -delete
find .ros2-stubs -name "__pycache__" -type d -exec rm -rf {} +
```

### 디버깅

로컬 인터프리터로는 디버깅이 안 된다. 브레이크포인트가 필요하면
`Add Interpreter → On Docker Compose` (service: `api`, path: `/usr/bin/python3`) 로
별도 인터프리터를 하나 더 만들어 실행 구성에서만 쓴다.

## 다른 ROS2 노드 붙이기

macOS 의 Docker Desktop 은 `network_mode: host` 를 지원하지 않는다. 대신
`ssorry-rosnet` 이라는 외부 공유 네트워크를 쓰므로, 다른 ROS2 컨테이너를
같은 네트워크에 띄우면 DDS 디스커버리가 서로를 찾는다.

```bash
docker run --rm -it \
  --network ssorry-rosnet \
  -e ROS_DOMAIN_ID=0 \
  -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  ros:humble-ros-base \
  ros2 topic echo /chatter
```

`ROS_DOMAIN_ID` 와 `RMW_IMPLEMENTATION` 은 `api` 서비스와 반드시 같아야 한다.
다른 compose 프로젝트에서 붙일 때도 네트워크를 `external: true` / `name: ssorry-rosnet`
으로 선언하면 된다.
