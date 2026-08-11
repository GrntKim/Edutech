FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# A2(curriculum_search)가 검색 시 쓰는 임베딩 모델(KoE5, 버전 고정)을 빌드 시점에
# 미리 받아 이미지에 캐시해둔다. app/agents/curriculum_search/logic.py가
# HF_HUB_OFFLINE=1을 기본 설정하는데(런타임에 "최신 버전 확인" 네트워크 호출을
# 생략해 콜드스타트 지연을 줄이려는 목적), 이미지에 캐시가 없으면 그 설정 때문에
# 오히려 모델을 아예 못 받아 첫 요청이 실패한다 — 이 단계가 그 전제조건을 채운다.
# requirements.txt가 안 바뀌면 이 레이어도 캐시되어 재빌드 시 다시 안 받는다.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('nlpai-lab/KoE5')"

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
