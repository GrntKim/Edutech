# app/static/vendor/

외부 라이브러리를 CDN 대신 저장소에 직접 담아둔다.

CDN(unpkg 등) 장애나 네트워크 문제가 곧바로 서비스 장애가 되는 것을 막고,
네트워크 없이도 화면이 정상 동작하게 하는 것이 목적이다. 커밋되어 있어야
`Dockerfile`의 `COPY . .`로 배포 이미지에 포함된다 — `.gitignore`·`.dockerignore`
어느 쪽에도 걸리지 않는지 확인할 것.

## 목록

| 파일 | 버전 | 원본 |
| --- | --- | --- |
| `htmx.min.js` | 2.0.4 | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js |

## 업그레이드 절차

1. 새 버전을 내려받아 파일을 교체한다.
   ```bash
   curl -o app/static/vendor/htmx.min.js https://unpkg.com/htmx.org@<버전>/dist/htmx.min.js
   ```
2. 파일 첫 줄의 버전 주석을 새 버전·URL로 갱신한다. 내려받은 원본에는 이 주석이
   없으므로 교체할 때마다 다시 넣어야 한다.
   ```
   /* htmx <버전> — https://unpkg.com/htmx.org@<버전>/dist/htmx.min.js */
   ```
3. 위 표의 버전을 갱신한다.
4. 화면 동작을 확인한다 — 교안 생성 폼(`hx-post`), 진행 표시 폴링,
   `hx-boost` 링크 이동, 브라우저 뒤로/앞으로.
