import os
from typing import TypeVar

import google.generativeai as genai
from dotenv import load_dotenv
from pydantic import BaseModel

MODEL_NAME = "gemini-2.5-flash"

ModelT = TypeVar("ModelT", bound=BaseModel)

load_dotenv()


def generate_json(system_instruction: str, prompt: str, response_model: type[ModelT]) -> ModelT:
    """C(lesson_generate) 자체 Gemini 클라이언트.

    app/lib/gemini.py는 D(REQ-005) 담당이며 아직 비어 있어, 그 파일을 건드리지
    않기 위해 동일한 시그니처로 lesson_generate 폴더 안에 자체 구현한다.
    D가 lib/gemini.py를 완성하면 logic.py의 import만 그쪽으로 바꿔서 이 파일은
    제거하면 된다.
    """
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=system_instruction,
    )
    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=response_model,
        ),
    )
    return response_model.model_validate_json(response.text)
