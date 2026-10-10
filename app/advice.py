"""AI 조언: 지금 대시보드 상태(통계, 최근 판정, 미션 진행, 로봇팔 / 카메라)를 Claude 에 보내
운영자가 지금 무엇을 하면 좋을지 짧게 조언받는다.

API 키는 환경변수 ANTHROPIC_API_KEY (서버 .env). 없으면 503.
공개 주소에 열려 있으므로 질문 없는 요청은 ADVICE_MIN_INTERVAL_S 안에 다시 오면 직전 조언을 그대로 돌려준다
(누가 버튼을 연타해도 API 비용이 늘지 않게).
"""

import asyncio
import json
import logging
import time

import anthropic

from app import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """너는 사과 선별 로봇 대회 현장의 운영 보조다. 로봇(AgileX PIPER 6축 팔)이 트레이의 사과를 카메라로 찾아 집고,
등급(상 / 중 / 하)을 매겨 상자 칸에 놓는다. 운영자는 대시보드를 보며 로봇을 감독한다.

받는 데이터:
- stats: 이번 회차 등급별 누적 개수, cycle_time: 사과 하나에 걸린 초
- recent_judges: 최근 판정. v_value = 빨강 비율(0~1), threshold = 상 기준, confidence = 기준에서 떨어진 정도(0.5~1, 확률 아님),
  extra 에 흠 / 멍 비율과 기준, roll_detected = 놓은 뒤 굴렀는지 (null = 아직 모름)
- mission: status(idle / running / paused(운영자가 정지, 이어서 가능) / stalled / finished / estop), 진행(apple_index / apple_count), phase, 파지 성공·실패·건너뜀,
  adaptive(굴림이 나면 하강 속도 배율 scale 과 놓는 높이 release_h 를 낮춘다. frozen 이면 자동 조정이 멈춘 상태)
- recent_events: 최근 미션 이벤트 (skip, drop, collision(부딪혀 원위치 후 재시도), pause, estop, stalled 등)
- arm: 로봇팔 연결 상태, cameras: 카메라가 프레임을 받고 있는지

답하는 방법:
- 한국어 존댓말, 짧게. 운영자가 서서 바로 읽고 움직일 수 있게.
- 위험하거나 멈춘 상황(비상정지, stalled, 로봇팔 / 카메라 끊김, frozen)이 있으면 그것을 맨 먼저 말한다.
- 지금 할 일을 최대 3가지, 각각 근거 숫자와 함께. 데이터에 없는 것은 추측하지 말고 모른다고 한다.
- 문제가 없으면 "정상"이라고 한 줄로 말하고 눈여겨볼 점 하나만 덧붙인다.
- 운영자의 질문이 있으면 그 질문에 먼저 답한다."""


class AdviceUnavailable(Exception):
    """API 키가 없거나 API 호출이 실패해 조언을 만들 수 없다. 메시지는 화면에 그대로 보여 줄 수 있다."""


class Advisor:
    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
        self._lock = asyncio.Lock()
        self._last: dict | None = None
        self._last_at = 0.0

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if not config.ANTHROPIC_API_KEY:
            raise AdviceUnavailable("AI 조언을 쓰려면 서버 .env 에 ANTHROPIC_API_KEY 를 넣어 주세요")
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=30.0, max_retries=1)
        return self._client

    async def advise(self, context: dict, question: str | None = None) -> dict:
        question = (question or "").strip()[:500] or None
        async with self._lock:
            # 질문 없는 요청이 짧은 간격으로 반복되면 직전 조언을 돌려준다 (비용 보호)
            if question is None and self._last is not None and time.time() - self._last_at < config.ADVICE_MIN_INTERVAL_S:
                return {**self._last, "cached": True}

            client = self._get_client()
            user = "현재 상태(JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)
            if question:
                user += f"\n\n운영자 질문: {question}"
            try:
                response = await client.messages.create(
                    model=config.ADVICE_MODEL,
                    max_tokens=1500,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user}],
                )
            except anthropic.AuthenticationError as e:
                raise AdviceUnavailable("API 키가 올바르지 않습니다 (ANTHROPIC_API_KEY 확인)") from e
            except anthropic.NotFoundError as e:
                raise AdviceUnavailable(f"모델을 찾을 수 없습니다: {config.ADVICE_MODEL} (ADVICE_MODEL 확인)") from e
            except anthropic.RateLimitError as e:
                raise AdviceUnavailable("요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요") from e
            except anthropic.APIStatusError as e:
                log.warning("AI 조언 API 오류 %s: %s", e.status_code, e.message)
                raise AdviceUnavailable(f"AI 서버 오류 ({e.status_code}). 잠시 뒤 다시 시도해 주세요") from e
            except anthropic.APIConnectionError as e:
                raise AdviceUnavailable("AI 서버에 연결할 수 없습니다 (네트워크 확인)") from e

            if response.stop_reason == "refusal":
                raise AdviceUnavailable("AI 가 이 요청에 답하지 않았습니다")
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            result = {
                "advice": text,
                "question": question,
                "model": response.model,
                "generated_at": time.time(),
                "cached": False,
            }
            log.info("AI 조언 생성 (%s, 입력 %d / 출력 %d 토큰)", response.model,
                     response.usage.input_tokens, response.usage.output_tokens)
            if question is None:
                self._last, self._last_at = result, time.time()
            return result
