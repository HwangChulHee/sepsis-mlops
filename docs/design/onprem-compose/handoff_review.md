# handoff_review.md — On-Prem Compose 통합 구현 핸드오프 레드팀 검토

- **대상**: `docs/design/onprem-compose/handoff.md` (구현 핸드오프)
- **핵심 질문**: [확인됨] 태그가 src 실물과 일치하는가 / §0 entrypoint 대체가 M2-2를 온전히 대신하는가 / CG-1~7이 spec-writer RED로 옮겨질 만큼 정확·관측가능한가 / 설계→코드 번역 오류(반환타입·결측 계약·동시성·restart 시맨틱)가 있는가

---

## 라운드 1 (redteam)

- **대상 commit**: handoff v1 · **검토일**: 2026-07-02
- **판정**: **HOLD — blocker 1건** (major 2, minor 4)

### PASS (실물 대조로 확인)

- **CMD 인자 정합** — §2.1 기존 CMD 인용이 `deploy/Dockerfile:36`과 문자 단위 일치. entrypoint가 `--log-level ${LOG_LEVEL:-info}` 보존 계약 반영됨.
- **alias 해석 규칙** — `app.py:45-46` `_resolve_alias` = `(ARTIFACTS / f"gru_{fs}").resolve()`. entrypoint `$ARTIFACTS_DIR/gru_$FS` + `SERVE_FEATURESET` 기본 `vitals`(`app.py:73`) 일치.
- **seed 명령 형태** — `h4s_export_bundle.py:76-80` `main()`이 `sys.argv[1:] or ["vitals","vitals_labs"]`, 인자 없으면 vitals_labs 순회하다 RuntimeError 비-0. `vitals` 단일 positional 정확.
- **Dockerfile.api build context/auditdb 미생성** — `:24 COPY src/`(context=루트 필수), `:25 mkdir -p /app/deploy/artifacts`뿐 `/app/auditdb` 부재. B2-2 chown 처방 대상·named volume 소유권 상속 정합.
- **console/api.py 라우트** — `/console/versions`·`/versions/{version}`·`/audit`·`/approve`·`/rollback` 5개, `/health` 부재(`:44,49,54,77,87`). serving `/health`는 `app.py:110` 실재. CG-5 정확.
- **CG-5 빈상태 200 (코드정독 해소)** — `list_versions`(`service.py:222-240`)가 `deploy.active_version`(심링크 없으면 None) + `_scan_version_ids`(없으면 `[]`)로 예외 없이 dict 반환 → 번들 0개에서도 200. (alpine 런타임 실측만 SM-4 잔존)
- **동시성 무경합** — console-api lifespan(`service.py:157-161`)이 `yield` 전 `_reconcile_or_seed` 완료, FastAPI는 startup 후 요청 수신 → healthcheck가 부팅 화해와 무경합. read/append라 레이스 없음.
- **SERVE_URL 배선·prometheus 타깃** — `service.py:167,175` 기본 localhost → CG-3 정당. `prometheus.yml:13 host.docker.internal:8000` → CG-6 재배선 정당.

### blocker (1건)

**B1 / serving `restart` 정책 처방이 Docker 시맨틱과 어긋나고 애매 — §0 핵심 산출물(읽을 수 있는 실패) 파괴**

- **문제**: §2.1(handoff:57)이 "serving은 `restart: "no"` **또는** `unless-stopped`+종료코드 무시로, 3번 종료는 재기동 안 하도록"이라 두 옵션을 동격 확정. 그러나 **Docker/Compose restart 정책엔 종료코드별 재시작 억제 기능이 없다** — `unless-stopped`는 종료코드 무관 **항상** 재시작(= exit 3에도 재시작 = 무한 crash-loop). "unless-stopped면 exit 3 재기동 안 함"은 사실 오류이고, §0이 대체하려는 목표(seed 미완을 crash-loop이 아닌 읽을 수 있는 정지로)를 정면으로 깬다.
- **근거**: handoff:51,57(exit 3 + unless-stopped 종료코드 무시) · `decisions.md` 결정 7 미결이 "unless-stopped+exit3=진짜 crash-loop"을 **정확히 이해**했는데 핸드오프가 "확정"하며 오류를 재도입. 종료코드로 "구분"은 systemd `RestartPreventExitStatus`/K8s 개념이지 Docker 불가. CG-1~7 어디에도 serving restart 강제 계약 없음 → spec-writer RED로 못 잡고 SM-6(런타임)에서야 드러남. 구현자가 `unless-stopped`(DDD가 POC 기본으로 언급) 고르면 §0 지배 산출물이 crash-loop 붕괴.
- **제안**: (1) serving restart를 **`restart: "no"` 단일 확정**, "unless-stopped+종료코드 무시" 분기 삭제(`on-failure`도 비-0 재시작이라 부적격). (2) "실크래시 auto-restart"와 "precondition exit 3 no-crash-loop"는 Docker에서 동시 불가임을 트레이드오프로 명시(POC는 전자 포기). (3) **CG-8** 신설: serving `restart`가 `always`/`unless-stopped`/`on-failure`가 아님(=`"no"` 또는 부재)을 파싱 강제 → SM-6 이전 RED로.
- **[reviser 응답]** 해소. (1) §2.1 "종료코드 정책 (B1 확정)"을 **`restart: "no"` 단일값**으로 재작성, "unless-stopped+종료코드 무시" 서술 삭제 — "Docker restart 정책엔 종료코드별 억제가 없다(그건 systemd `RestartPreventExitStatus`/K8s)"를 근거로 명시. §2.2 serving 행 핵심 셀에도 `restart: "no"` 명기. (2) 같은 절에 **트레이드오프** 추가: "실크래시 auto-restart ⊥ precondition exit 3 no-crash-loop 동시 불가 → POC는 auto-restart 포기, auto-restart 필요 시 K8s/systemd 승격". (3) §3.2에 **CG-8 신설** — serving `restart`가 `always`/`unless-stopped`/`on-failure`가 아님(=`"no"` 또는 부재)을 정적 파싱으로 강제, "SM-6 이전 RED" 명시. §0 지배 산출물 가드임을 CG-2 주석 밑에 병기. entrypoint 코드 블록의 exit 3 주석도 "restart:"no"와만 결합해야 crash-loop 없음"으로 정정.

### major (2건)

- **M1 / exec-form ENTRYPOINT인데 스크립트 shebang·실행권한 미명세** — §2.1이 `ENTRYPOINT ["/app/deploy/serving-entrypoint.sh"]`(exec form)인데 스크립트 본문(handoff:46-53)에 shebang(`#!/bin/sh`) 없고 `chmod +x`/COPY 권한 언급 없음. exec form은 커널 직접 exec이라 shebang 없거나 비실행이면 "exec format error"로 컨테이너 미기동. 부수: §3.1 테스트가 `/bin/sh script`로 부르면 shebang 없이 GREEN이지만 컨테이너 실패(번역 괴리). 제안: 스크립트 `#!/bin/sh` + Dockerfile `chmod +x`, 또는 `ENTRYPOINT ["/bin/sh","/app/deploy/serving-entrypoint.sh"]`(기존도 `/bin/sh -c`라 최소변경).
- **[reviser 응답]** 해소. §2.1 "Dockerfile 변경 (M1 확정)"을 **`ENTRYPOINT ["/bin/sh", "/app/deploy/serving-entrypoint.sh"]`로 확정** — `deploy/Dockerfile:36`이 `["/bin/sh","-c","exec uvicorn ..."]`임을 [확인됨]으로 대조하고 최소변경 근거로 채택. "exec form에 스크립트 경로만 넣으면 shebang·chmod 없이 exec format error"를 명시. 스크립트 코드 블록 첫 줄에 `#!/bin/sh` 병기(이중 안전장치)하고, 기동 보장은 ENTRYPOINT 형태가 담당함을 주석. §3.1에 **번역 괴리** 항목 추가: "`/bin/sh script`로 부르는 유닛은 shebang 없이 GREEN이나 컨테이너 기동은 ENTRYPOINT 형태에 달림 → CG/SM-6이 실제 기동 담당".
- **M2 / CG-2가 healthcheck 존재만 강제, 정확성(엔드포인트·포트)은 계약 공백** — CG-2는 console-web healthcheck가 **없을** 때만 잡음. 그러나 B3-1 실제 함정은 "잘못된 포트/엔드포인트면 영구 unhealthy". CG-5는 console-api·serving 정확성만, console-web(8080,`/`)·front-nginx 대응 CG 없음. 잘못된 포트로 짜면 CG 전부 통과·런타임 front-nginx 영구 미기동(B3-1 재발). 제안: CG-5 확장해 console-web(8080/`/`)·front-nginx(`/`) 포트·엔드포인트도 파싱 강제(§2.2 표에 값 이미 있음). 명령 바이너리 존재까지는 SM 한계로 명시.
- **[reviser 응답]** 해소. §3.2 **CG-5를 "엔드포인트·포트 정합, M2 확장"으로 재작성** — 존재가 아니라 정확성을 강제하도록 4개 서브불릿: console-api(`/console/versions?fs=vitals`)·serving(`/health`,8000)·**console-web(8080,`/`)·front-nginx(`/`)**. 각 값은 §2.2 표 근거. "정적 YAML 파싱은 문자열만 보고 명령 바이너리 실재·실제 200은 못 봄 → SM-4/SM-6 몫"을 **SM 한계로 명시**.

### minor (4건)

- **m1 (§0 한계 서술, 안전측)**: `-e`는 심링크 따라가므로 dangling 심링크(대상 삭제)는 부재 판정→exit 3으로 잡힘 → §0 함의보다 견고. "번들 내부 파일 손상(dir 실재, model.pt 깨짐)" sub-case만 stuck-unhealthy로 남음 → §0 한계 문구를 이 sub-case로 좁히면 정확.
- **[reviser 응답]** 반영. §0 "한계(정직, m1 정정)"을 재작성 — "`-e`가 dangling 심링크까지 부재로 잡아 §0 대체가 더 견고", 남는 한계는 "alias·대상 dir 실재하되 **번들 내부 파일 손상**"만으로 좁힘.
- **m2 (serving healthcheck start_period/timeout 미명세)**: `/health` 첫 프로브가 300-trial lazy 트리거(`app.py:72-73,110-113`). §2.2에 `start_period`(≥캘리브레이션)·`timeout` 값 없음. serving을 `service_healthy`로 게이트하는 다운스트림 없어(console-api=service_started) 파급은 없으나 값 명시 권장(DDD 결정 7 "start_period로 커버").
- **[reviser 응답]** 반영. §2.2 serving 행 셀에 `start_period`(≥캘리브레이션)·`timeout` 표기 + **"serving healthcheck 타이밍 (m2)"** 전용 불릿 추가(결정 7 "start_period로 커버" 인용, `start_period≈300s`·`timeout` 짧게, start_period 창 실패는 unhealthy 미집계).
- **m3 (busybox wget exit 계약)**: console-api healthcheck `wget -qO- .../console/versions?fs=vitals`는 200에서만 exit 0. exec-form 인자에 `?fs=vitals`가 리터럴 전달(셸 미경유)임을 §3에 한 줄 명시 권장.
- **[reviser 응답]** 반영. §2.2 console-api healthcheck 셀에 "exec-form 인자라 `?fs=vitals` **리터럴 전달=셸 미경유**" 주석 추가(§3.2 CG-5 정합).
- **m4 (출제자-응시자, 경미)**: §2 src 라인 참조가 §3 RED 대상과 한 문서에 섞였으나 **테스트 대상 산출물(compose.yml·entrypoint.sh·nginx conf)은 전부 신규**라 src 라인은 "기대값 근거"일 뿐 해답 누수 아님. 분리 불필요 수준이나 원하면 §2(구현참조)·§3(계약) 경계 한 줄 명시.
- **[reviser 응답]** 반영. §3 상단에 경계 한 줄 추가: "§2=구현참조(src 라인=기대값 근거), §3=계약(RED 규칙); 대상 산출물 전부 신규라 src 인용은 해답 누수 아님".

### 구현 검증 항목 (런타임 실측 — 스모크 SM-1~7 이미 이관, blocker 아님)

핸드오프 §4 스모크 체크리스트가 이미 전수 이관. 추가 없음.

---

**blocker: 1건** (B1). blocker≠0 → **HOLD**. B1은 §0 대체가 노린 핵심 산출물(읽을 수 있는 실패)을 구현자 선택에 따라 crash-loop으로 붕괴시키며 계약 테스트로도 안 걸림 — reviser가 (1) serving `restart:"no"` 확정, (2) 잘못된 서술 삭제, (3) CG-8 신설로 해소해야 진행 가능. major 2·minor 4 병행 보완 권장.
