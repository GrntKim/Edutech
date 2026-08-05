# 소유: E(REQ-006)
"""Gemini API 공통 호출 래퍼.

A1·A2·B·C·D 전 에이전트가 이 모듈을 경유해서만 Gemini를 호출한다
(팀 규약 — google.genai 직접 import 금지).

SDK: google-genai(신 SDK). thinking_level(minimal/low/medium/high)로
temperature를 대체한다 — Gemini 3 계열부터 temperature 대신 thinking 강도로
추론량을 조절하는 방식으로 바뀌었다.

재시도 계층 분리 원칙:
  - 이 모듈의 max_retries는 API 레벨 실패(타임아웃/429/5xx)만 재시도한다.
    google-genai SDK 자체 재시도(retry_options)는 쓰지 않는다 — 이중 재시도로
    60초 파이프라인 예산이 무너지는 걸 막기 위해 이 모듈이 유일한 재시도 계층이다.
  - 응답이 response_schema로 파싱되지 않는 경우는 절대 재시도하지 않고
    GeminiSchemaError를 그대로 올린다. 스키마 재생성 재시도는 호출한 에이전트의
    책임이다(예: A1은 자체적으로 최대 2회 재생성하고 그 횟수를
    ConceptCollectResult.retry_count에 기록한다).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Literal, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# 모델 문자열 확정: gemini-3.6-flash. NFR-001-6(재현성 — 모델 버전 고정, 변경 시
# 골든셋 재측정)을 만족하려면 alias("-latest")가 아니라 이렇게 버전 고정 문자열이어야 한다.
# 주의: 이 문자열은 실제 API 호출로 검증되지 않았다 — 첫 통합 시 존재 여부 확인 필요.
DEFAULT_MODEL = "gemini-3.6-flash"

ThinkingLevel = Literal["minimal", "low", "medium", "high"]

_API_BACKOFF_BASE_S = 1.0
_QUOTA_BACKOFF_BASE_S = 5.0

T = TypeVar("T", bound=BaseModel)


class GeminiError(Exception):
    """Gemini 호출 관련 모든 예외의 기반 클래스."""


class GeminiTimeoutError(GeminiError):
    """요청 시간 초과(재시도 소진 후)."""


class GeminiQuotaError(GeminiError):
    """429 쿼터 초과(재시도 소진 후)."""


class GeminiSchemaError(GeminiError):
    """응답을 response_schema로 파싱·검증하지 못함. 재시도하지 않는다."""


_client: genai.Client | None = None


def _resolve_api_key() -> str:
    """.env → 환경변수 → Secret Manager 순으로 API 키를 조회한다."""
    # 함수 안에서 import하는 이유: 테스트가 monkeypatch.setattr("dotenv.load_dotenv", ...)로
    # 모듈 속성을 패치하므로, 매 호출 시점에 현재 바인딩을 다시 조회해야 패치가 먹는다.
    from dotenv import load_dotenv

    load_dotenv()

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key

    secret_name = os.environ.get("GEMINI_API_KEY_SECRET_NAME")
    if secret_name:
        try:
            from google.cloud import secretmanager
        except ImportError as exc:
            raise GeminiError(
                "GEMINI_API_KEY_SECRET_NAME이 설정되어 있으나 "
                "google-cloud-secret-manager가 설치되어 있지 않습니다. "
                "requirements.txt에 추가하세요."
            ) from exc
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=secret_name)
        return response.payload.data.decode("utf-8")

    raise GeminiError(
        "Gemini API 키를 찾을 수 없습니다. .env 또는 환경변수 GEMINI_API_KEY"
        "(또는 GOOGLE_API_KEY)를 설정하거나, Secret Manager 사용 시 "
        "GEMINI_API_KEY_SECRET_NAME을 설정하세요."
    )


def _get_client() -> genai.Client:
    """클라이언트를 모듈 레벨에서 1회 생성해 재사용한다(매 호출마다 새로 만들지 않음).
    API 키 조회는 첫 호출 시점까지 늦춘다(테스트에서 import만 하는 경우 보호)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=_resolve_api_key())
    return _client


def _classify(exc: Exception) -> GeminiError:
    """예외를 분류만 한다(sleep 없음). 분류 불가한 예외는 그대로 다시 올린다(뭉개지 않음)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return GeminiTimeoutError(f"Gemini 요청 시간 초과: {exc}")
    if isinstance(exc, errors.APIError):
        # code 속성명은 SDK 버전에 따라 달라질 수 있어 방어적으로 조회한다.
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 429:
            return GeminiQuotaError(f"Gemini 쿼터 초과(429): {exc}")
        return GeminiError(f"Gemini API 호출 실패: {exc}")
    raise exc


def _backoff_seconds(error: GeminiError, attempt: int) -> float:
    """분류된 에러 종류에 맞는 지수 백오프 대기 시간(초). 429는 더 길게 기다린다."""
    base = _QUOTA_BACKOFF_BASE_S if isinstance(error, GeminiQuotaError) else _API_BACKOFF_BASE_S
    return base * (2**attempt)


def _call_generate(
    *,
    prompt: str,
    model: str,
    thinking_level: ThinkingLevel,
    timeout_s: float,
    max_retries: int,
    prompt_version: str | None,
    response_mime_type: str | None = None,
    response_schema: type[BaseModel] | None = None,
) -> str:
    """API 레벨 실패(타임아웃/429/5xx)만 재시도하는 공통 호출부. 원문 텍스트를 반환한다."""
    client = _get_client()
    # response_schema를 SDK에 그대로 넘긴다. SDK가 스키마 위반을 감지해 APIError로
    # 던지면 _classify가 이를 API 실패로 오분류해 재시도할 위험이 있다 — 재시도 계층
    # 분리 원칙이 깨지는 지점이니 통합 테스트에서 실제 동작을 확인할 것. 문제가 되면
    # response_schema는 넘기지 말고 response_mime_type만으로 JSON을 강제한 뒤
    # 파싱은 전부 이 모듈에서 하는 방식으로 바꾼다.
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
        response_mime_type=response_mime_type,
        response_schema=response_schema,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )

    last_error: GeminiError | None = None
    for attempt in range(max_retries + 1):
        start = time.monotonic()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:  # 분류는 _classify가 담당
            elapsed_ms = (time.monotonic() - start) * 1000
            last_error = _classify(exc)
            logger.warning(
                f"gemini_call_failed model={model} elapsed_ms={elapsed_ms:.1f} "
                f"retry_count={attempt} error={last_error}",
                extra={
                    "model": model,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "retry_count": attempt,
                    "prompt_len": len(prompt),
                    "prompt_version": prompt_version,
                    "error": str(last_error),
                },
            )
            if attempt < max_retries:
                time.sleep(_backoff_seconds(last_error, attempt))
            continue

        elapsed_ms = (time.monotonic() - start) * 1000
        text = response.text
        if text is None:
            # 안전 필터 차단이나 비정상 종료(finish_reason != STOP)면 text가 None이다.
            # API 레벨 실패가 아니므로 재시도하지 않고 바로 올린다.
            raise GeminiError(
                "Gemini가 빈 응답을 반환했습니다(안전 필터 또는 비정상 종료): "
                f"{getattr(response, 'candidates', None)}"
            )
        logger.info(
            f"gemini_call_success model={model} elapsed_ms={elapsed_ms:.1f} "
            f"retry_count={attempt}",
            extra={
                "model": model,
                "elapsed_ms": round(elapsed_ms, 1),
                "retry_count": attempt,
                "prompt_len": len(prompt),
                "prompt_version": prompt_version,
            },
        )
        return text

    assert last_error is not None
    raise last_error


def generate_structured(
    prompt: str,
    response_schema: type[T],
    *,
    model: str = DEFAULT_MODEL,
    thinking_level: ThinkingLevel = "low",
    timeout_s: float = 30.0,
    max_retries: int = 2,
    prompt_version: str | None = None,
) -> T:
    """구조화 출력(JSON) 강제 호출.

    response_schema로 파싱·검증된 인스턴스를 반환한다. 파싱/검증 실패는
    재시도하지 않고 GeminiSchemaError로 즉시 올린다 — 재생성 재시도는
    호출한 에이전트(A1/A2/B/C/D)의 책임이다.

    사용처: A1 개념 생성/쿼리 재작성, A2 리랭킹, B 매핑, C 교안 생성, D 2단계 검증.
    """
    raw_text = _call_generate(
        prompt=prompt,
        model=model,
        thinking_level=thinking_level,
        timeout_s=timeout_s,
        max_retries=max_retries,
        prompt_version=prompt_version,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    try:
        data = json.loads(raw_text)
        return response_schema.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GeminiSchemaError(
            f"Gemini 응답을 {response_schema.__name__}로 파싱하지 못했습니다: {exc}"
        ) from exc


def generate_text(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    thinking_level: ThinkingLevel = "low",
    timeout_s: float = 30.0,
    max_retries: int = 2,
    prompt_version: str | None = None,
) -> str:
    """자유 텍스트 반환."""
    return _call_generate(
        prompt=prompt,
        model=model,
        thinking_level=thinking_level,
        timeout_s=timeout_s,
        max_retries=max_retries,
        prompt_version=prompt_version,
    )


# 이미지 생성 모델은 무료 등급 쿼터가 0이라 결제 연결된 프로젝트에서만 동작 확인됨
# (2026-08-04 실측: 429 limit=0). Imagen 전용 generate_images()는 SDK가 deprecated
# 처리해 generate_content로 이미지 모델을 호출하는 방식을 쓴다. C(lesson_generate)
# 요청으로 결제 연결 확인 후 예시 삼아 한 번 추가함 — E와 정식 인터페이스 협의 필요.
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"


def generate_image(
    prompt: str,
    *,
    model: str = DEFAULT_IMAGE_MODEL,
    timeout_s: float = 60.0,
    max_retries: int = 2,
    prompt_version: str | None = None,
) -> bytes:
    """이미지를 생성해 원본 바이트(PNG/JPEG)를 반환한다. 텍스트 응답만 오거나 안전
    필터에 막히면 GeminiError를 올린다. 재시도 정책은 generate_structured/
    generate_text와 동일하게 API 레벨 실패만 재시도한다."""
    client = _get_client()
    config = types.GenerateContentConfig(
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )

    last_error: GeminiError | None = None
    for attempt in range(max_retries + 1):
        start = time.monotonic()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            last_error = _classify(exc)
            logger.warning(
                f"gemini_image_call_failed model={model} elapsed_ms={elapsed_ms:.1f} "
                f"retry_count={attempt} error={last_error}",
                extra={
                    "model": model,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "retry_count": attempt,
                    "prompt_version": prompt_version,
                    "error": str(last_error),
                },
            )
            if attempt < max_retries:
                time.sleep(_backoff_seconds(last_error, attempt))
            continue

        elapsed_ms = (time.monotonic() - start) * 1000
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            parts = getattr(candidate.content, "parts", None) or []
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None and inline_data.data:
                    logger.info(
                        f"gemini_image_call_success model={model} elapsed_ms={elapsed_ms:.1f} "
                        f"retry_count={attempt}",
                        extra={
                            "model": model,
                            "elapsed_ms": round(elapsed_ms, 1),
                            "retry_count": attempt,
                            "prompt_version": prompt_version,
                        },
                    )
                    return inline_data.data
        raise GeminiError(
            f"Gemini가 이미지를 반환하지 않았습니다(텍스트만 응답했거나 안전 필터 차단): {candidates}"
        )

    assert last_error is not None
    raise last_error
