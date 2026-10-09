# Ssorry — ROS2 + FastAPI 웹 서버

macOS(Apple Silicon)에는 rclpy 를 네이티브로 설치할 수 없어서, ROS2 Humble 컨테이너
(`ros:humble-ros-base`, Ubuntu 22.04 / Python 3.10) 안에서 FastAPI 를 돌린다.
PyCharm 은 이 컨테이너를 원격 인터프리터로 붙여서 rclpy 자동완성과 디버깅을 쓴다.

## 구조

```
Dockerfile           ros:humble + fastapi/uvicorn
docker-compose.yml   포트 8000, app/ 를 마운트해 --reload
requirements.txt     fastapi, uvicorn, pydantic
app/main.py          FastAPI 엔드포인트
app/ros_node.py      rclpy 노드 + spin 스레드 관리
app/stream.py        ROS 스레드 → asyncio 로 카메라 프레임 전달
app/sessions.py      로그인 없는 쿠키 세션
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

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 카메라 실시간 뷰어 (세션 쿠키 발급) |
| `GET /session` | 현재 세션 ID, 열린 웹소켓 수 |
| `WS /ws/camera` | 카메라 JPEG 프레임을 바이너리로 계속 전송 |
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
