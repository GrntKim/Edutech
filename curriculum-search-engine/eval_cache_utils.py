"""eval_*.py들이 재실행 시 이전 결과를 재사용할지 판단하는 공용 유틸.

기존엔 결과 파일에 골든셋 행 번호(`no`)만 저장해두고, 파일이 존재하면 그
행은 무조건 재사용했다. 코퍼스(curriculum_units.json)가 바뀌어도 결과
파일은 그대로 남아있어 바뀌기 전 코퍼스 기준 결과를 그대로 재사용해버리는
문제가 있었다(2026-08-11, 사회·국어 추가 후 회귀 체크 때 발견 — 288개
코퍼스 기준 결과가 424개 코퍼스 재실행인 것처럼 재사용될 뻔함). 결과 파일에
코퍼스 지문(fingerprint)을 같이 저장해두고, 재실행 시 지문이 다르면 캐시를
통째로 무시해서 이 실수가 조용히 반복되지 않도록 한다.
"""

import json
from hashlib import sha256
from pathlib import Path


def corpus_fingerprint(chunks_path: Path) -> str:
    """curriculum_units.json 내용의 sha256 앞 12자리. 코퍼스가 조금이라도
    바뀌면(청크 추가/삭제/내용 수정) 값이 달라진다."""
    return sha256(chunks_path.read_bytes()).hexdigest()[:12]


def load_cached_results(results_path: Path, current_fingerprint: str) -> dict[str, dict]:
    """캐시된 결과를 {no: row} 형태로 반환한다.

    파일이 없거나, 지문 정보 없는 예전 포맷(순수 리스트)이거나, 코퍼스 지문이
    다르면 빈 캐시로 취급하고 이유를 콘솔에 출력한다 — 조용히 무시하지 않는다,
    그게 원래 문제였다.
    """
    if not results_path.exists():
        return {}

    raw = json.loads(results_path.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        print(
            f"  (캐시 무시: {results_path.name}이 지문 정보 없는 예전 포맷 — "
            "코퍼스 일치 여부를 확인할 수 없어 전체 재실행)"
        )
        return {}

    cached_fingerprint = raw.get("meta", {}).get("corpus_fingerprint")
    if cached_fingerprint != current_fingerprint:
        print(
            f"  (캐시 무시: {results_path.name}의 코퍼스 지문({cached_fingerprint})이 "
            f"현재 코퍼스({current_fingerprint})와 달라 전체 재실행)"
        )
        return {}

    return {r["no"]: r for r in raw["results"]}


def save_results(results_path: Path, fingerprint: str, results: list[dict]) -> None:
    payload = {"meta": {"corpus_fingerprint": fingerprint}, "results": results}
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
