"""eval_*.py들이 재실행 시 이전 결과를 재사용할지 판단하는 공용 유틸.

기존엔 결과 파일에 골든셋 행 번호(`no`)만 저장해두고, 파일이 존재하면 그
행은 무조건 재사용했다. 코퍼스(curriculum_units.json)가 바뀌어도 결과
파일은 그대로 남아있어 바뀌기 전 코퍼스 기준 결과를 그대로 재사용해버리는
문제가 있었다(2026-08-11, 사회·국어 추가 후 회귀 체크 때 발견 — 288개
코퍼스 기준 결과가 424개 코퍼스 재실행인 것처럼 재사용될 뻔함).

처음엔 코퍼스(curriculum_units.json)만 지문에 넣었는데, 같은 날 골든셋
CSV 자체를 수정(국어 행 교체)하다가 같은 문제가 또 나왔다 — `no`는 CSV의
행 번호일 뿐이라, 골든셋 내용이 바뀌어도 같은 `no`면 옛 행의 결과(다른
개념·다른 정답)를 새 행 결과인 것처럼 재사용해버린다. 그래서 코퍼스뿐
아니라 골든셋 CSV까지 같이 지문에 넣는다 — 캐시 무효화 기준이 될 만한
입력 파일은 전부 여기 넣어야 한다는 게 이번에 재확인된 원칙이다.
"""

import json
from hashlib import sha256
from pathlib import Path


def corpus_fingerprint(*input_paths: Path) -> str:
    """넘겨준 파일들 내용을 이어붙인 sha256 앞 12자리. 이름은 유지하지만
    코퍼스(curriculum_units.json) 하나만이 아니라 골든셋 CSV 등 결과에
    영향을 주는 입력 파일을 전부 넘겨야 한다 — 하나라도 바뀌면 값이 달라진다."""
    hasher = sha256()
    for path in input_paths:
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:12]


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
