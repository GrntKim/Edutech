"""app/lib/gemini.py 유닛 테스트.

실제 Gemini API를 호출하지 않고 client.models.generate_content를 가짜 객체로 대체해 검증한다.
"""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from pydantic import BaseModel

from app.lib import gemini


class _DummySchema(BaseModel):
    x: int


class _RaisingModels:
    """generate_content 호출마다 지정된 예외를 던지는 가짜 client.models."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.call_count = 0

    def generate_content(self, *, model, contents, config):
        self.call_count += 1
        raise self._exc


class _TextModels:
    """generate_content 호출마다 고정된 text를 반환하는 가짜 client.models."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.call_count = 0

    def generate_content(self, *, model, contents, config):
        self.call_count += 1
        return SimpleNamespace(text=self._text)


def _fake_client(models) -> SimpleNamespace:
    return SimpleNamespace(models=models)


@pytest.fixture(autouse=True)
def _reset_gemini_state(monkeypatch):
    """모듈 전역 설정 상태·API 키 환경변수를 매 테스트마다 초기화한다."""
    monkeypatch.setattr(gemini, "_client", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_SECRET_NAME", raising=False)


def test_timeout_raises_gemini_timeout_error(monkeypatch):
    models = _RaisingModels(httpx.TimeoutException("deadline exceeded"))
    monkeypatch.setattr(gemini, "_get_client", lambda: _fake_client(models))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    with pytest.raises(gemini.GeminiTimeoutError):
        gemini.generate_text("prompt", max_retries=1)

    assert models.call_count == 2  # 최초 시도 + 재시도 1회


def test_quota_error_retries_with_backoff(monkeypatch):
    exc = errors.ClientError(
        429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}}
    )
    models = _RaisingModels(exc)
    monkeypatch.setattr(gemini, "_get_client", lambda: _fake_client(models))
    sleep_calls: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", lambda s: sleep_calls.append(s))

    with pytest.raises(gemini.GeminiQuotaError):
        gemini.generate_text("prompt", max_retries=1)

    assert models.call_count == 2
    # 마지막 시도(재시도 계획 없음) 후에는 잠들지 않는다 — 실패 시도당 한 번만.
    assert sleep_calls == [gemini._QUOTA_BACKOFF_BASE_S * (2**0)]


def test_invalid_json_raises_schema_error_without_retry(monkeypatch):
    models = _TextModels("이건 JSON이 아님")
    monkeypatch.setattr(gemini, "_get_client", lambda: _fake_client(models))
    monkeypatch.setattr(
        gemini.time,
        "sleep",
        lambda s: pytest.fail("스키마 파싱 실패는 재시도하면 안 됨"),
    )

    with pytest.raises(gemini.GeminiSchemaError):
        gemini.generate_structured("prompt", _DummySchema, max_retries=2)

    assert models.call_count == 1


def test_success_returns_parsed_instance(monkeypatch):
    models = _TextModels('{"x": 42}')
    monkeypatch.setattr(gemini, "_get_client", lambda: _fake_client(models))

    result = gemini.generate_structured("prompt", _DummySchema)

    assert result == _DummySchema(x=42)
    assert models.call_count == 1


def test_missing_api_key_fails_on_first_call_not_on_import(monkeypatch):
    # 이 시점까지 도달했다는 것 자체가 "import는 성공"의 증거.
    # 로컬 .env에 실키가 있을 수 있으므로 load_dotenv를 무력화해 결정적으로 만든다.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)

    with pytest.raises(gemini.GeminiError, match="API 키"):
        gemini.generate_text("prompt")
