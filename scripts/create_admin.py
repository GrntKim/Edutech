"""관리자 계정 시드 스크립트.

앱과 동일한 bcrypt 해싱(app.lib.auth.hash_password)을 써서 해시를 만들고
Cloud SQL Proxy 경유로 users에 role='admin'으로 INSERT한다. SQL Studio에
평문/손계산 해시를 직접 넣는 실수를 막기 위함이다.

사용법 (repo root에서, 반드시 -m으로 — 그냥 python scripts/create_admin.py로 실행하면
repo root가 sys.path에 없어 "ModuleNotFoundError: No module named 'app'" 발생):
    python -m scripts.create_admin --email admin@example.com --password <pw> [--name 관리자]
"""

import argparse
import sys

from app.lib import db
from app.lib.auth import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="관리자 계정 생성")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="관리자")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("비밀번호는 8자 이상이어야 합니다.", file=sys.stderr)
        return 1

    try:
        user = db.create_user(
            email=args.email.strip().lower(),
            password_hash=hash_password(args.password),
            name=args.name,
            role="admin",
        )
    except db.EmailAlreadyExistsError:
        print(f"이미 가입된 이메일입니다: {args.email}", file=sys.stderr)
        return 1

    print(f"관리자 계정 생성 완료: id={user.id} email={user.email} role={user.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
