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

---

## 라운드 2 (redteam)

- **대상 commit**: handoff v2 (R1 반영) · **검토일**: 2026-07-02
- **핵심 질문**: R1 보완(B1·M1·M2)이 가짜 수렴 없이 해소됐나 / v2가 새로 만든 결함·아직 못 본 층이 있나
- **판정**: **HOLD — blocker 1건** (major 0, minor 4)

### PASS (R1 보완 실물 검증 + v2 새 처방)

- **B1 완전 해소** — §2.1이 serving restart를 `restart: "no"` 단일값으로 재작성, "unless-stopped+종료코드 무시" 잔재 없이 삭제. 근거·트레이드오프 명시. §2.2 serving 행도 반영. **CG-8** 신설 — `restart ∉ {always,unless-stopped,on-failure}`. exit 3 후 컨테이너 정지·재기동 없음 = §0 "읽을 수 있는 실패" 보존. decisions.md 결정 7 미결(비재시작 종료코드 구분)을 핸드오프가 "restart 끄기로 해소"로 종결 — decisions.md:153이 위임했으므로 권위 충돌 없음.
- **M1 해소 + 종료코드 전파 온전** — `ENTRYPOINT ["/bin/sh","/app/deploy/serving-entrypoint.sh"]` 확정, 기존 `deploy/Dockerfile:36`과 형태 정합. `#!/bin/sh` 병기. exit 3 전파 온전(`/bin/sh script`가 exit 3 그대로 전파), `exec uvicorn`이 sh(PID1)→uvicorn 교체로 SIGTERM graceful 보존. §3.1 번역괴리 노트 추가.
- **M2 해소** — CG-5가 정확성 강제로 확장: console-api·serving·**console-web(8080,`/`)·front-nginx(`/`)**. 실물 대조(console-web `listen 8080`·SPA 폴백, api.py `/health` 부재) 정합. SM 한계 명시.
- **entrypoint 테스트 §3.1 실현가능** — subprocess `sh entrypoint.sh`, 빈 ARTIFACTS_DIR→returncode==3+stderr assert, alias 케이스는 PATH 앞 가짜 uvicorn으로 exec 도달 확인. spec-writer가 쓸 만큼 구체적.
- **build context=루트 무해** — 두 Dockerfile 모두 원래 `COPY src/`라 context=루트 요구. monitoring은 pre-built image라 영향 없음.
- **불변식 보존(restart/entrypoint 변경분)** — restart:"no"는 재기동 정책일 뿐 in-process 핫스왑과 무관. entrypoint 인자·PID1 exec 보존.

### blocker (1건)

**B2-R2 / §2.5 auditdb `chown 10001` 처방이 compose `user:`/USER 없이 내부 모순 — 설계가 "반드시 확정"하라 위임한 artifacts 쓰기권한 정합이 통째로 누락**

- **문제**: §2.5(handoff:102)는 `Dockerfile.api`에서 `/app/auditdb`를 `chown 10001`로 선생성해 named volume이 **uid 10001** 소유권을 상속하게 하라 처방. 그러나 핸드오프 어디에도 `user: "10001"`이 없고(전체 grep 0건), `Dockerfile.api`·`deploy/Dockerfile` 둘 다 **USER 지시어 없음**(실물 확인 — 빌드·런타임 root 기본). 두 갈래 모두 미해결:
  - **(a) root 실행(문자 그대로)**: root라 chown 10001은 죽은 처방(root는 아무 데나 씀), 결정 6 "비-root" 조용히 위반, B2-2가 상정한 uid 10001 전제 소멸.
  - **(b) uid 10001 실행**: `user: "10001"` 명시 필요한데 없음. 게다가 **artifacts bind mount 호스트측 소유권 정합**(`chown 10001 ./deploy/artifacts`)이 필요한데 §2.2 volumes·§2.5 운영노트 어디에도 없음 → uid 10001이 승인 시 심링크 alias 스왑(`bundle.py set_alias`→`os.replace`)을 host-owned 폴더에서 시도 → **permission denied** → 승인 전파·**번들 원자성 불변식 붕괴**(SM-7 조용히 깨짐).
- **근거**: handoff:102(유일하게 10001 상정, `user:` 부재) · `Dockerfile.api`·`deploy/Dockerfile` USER 부재(실물) · **설계 위임 미이행**: decisions.md:69(결정 2 "artifacts 쓰기권한=필수 임계 경로 … 호스트 소유권+`user:` 정합"), decisions.md:125(결정 6 "비-root 택하면 `user:` 정합을 **핸드오프에서 반드시 확정**"). 핸드오프는 auditdb 절반(chown)만 옮기고 artifacts host-chown+`user:` 결정을 드롭. 이건 SM 항목 아님 — "어떤 uid로 돌리고 host chown하는가"의 **결정**은 핸드오프 몫(WSL2 uid 매핑 검증만 SM). artifacts 쓰기권한 커버 SM도 없음(SM-5=auditdb only, SM-1=심링크 해석뿐, SM-7=성공 가정).
- **chown 순서는 무문제**: `Dockerfile.api` USER 없어 chown이 root로 실행되는 순서 자체는 정상(대조: `console-web/Dockerfile:18-20` USER root→RUN→USER 101). 결함은 순서가 아니라 **어느 uid로 돌리는지·host-chown 누락**.
- **제안**: §2.2/§2.5에 확정 — (1) serving·console-api compose `user:` 명시(POC root면 §2.5 chown 삭제+결정 6 "비-root 유보" 명기; 비-root면 `user: "10001"`). (2) 비-root면 decisions.md:69 **필수** artifacts host-chown(`chown 10001 ./deploy/artifacts` 또는 seed uid=10001 정합)을 §2.2/운영노트 이관. (3) §2.5 chown 존치/삭제를 선택과 정합. auditdb·artifacts 두 임계 쓰기 경로가 같은 uid 전제 위에 일관되도록.
- **[reviser 응답]** 해소. **실물 재확인**: `deploy/Dockerfile`·`deploy/k8s/console/Dockerfile.api` 둘 다 **USER 지시어 없음(=root 기본)**, `console-web/Dockerfile`만 `USER 101`(비-root, artifacts·auditdb 미접근) — grep 직접 확인. **POC 스탠스=비-root `user: "10001:10001"` 확정** (권장안 채택): 설계가 이미 uid 10001 chown을 처방(결정 2 M-3·결정 5 B2-2)했고 K8s `runAsUser 10001`·`fsGroup 10001`과 정합하므로 root보다 이 방향이 일관. **(1)** §2.2 serving·console-api 행에 `user: "10001:10001"` 명시 + **★비-root 실행 전용 불릿** 추가(두 임계 쓰기 경로·console-web 예외 서술). **(2)** decisions.md:69 필수 artifacts host-chown(seed 후 `chown -R 10001:10001 ./deploy/artifacts` 또는 seed를 uid 10001로)을 §2.2 volumes 불릿 + §2.5 운영노트에 이관 — alias 스왑 permission denied·번들 원자성 붕괴 방지. **(3)** §2.5 auditdb chown 10001 **존치**로 정합(`user: "10001"`과 짝) + "현재 `Dockerfile.api:25`엔 auditdb·chown 없음, 이 줄 추가" 명기. **(4)** **CG-9 신설**(§3.2) — serving·console-api `user:` uid=10001 정적 파싱 강제(번들 원자성·감사 append-only 두 불변식 가드). **(5)** SM-5를 "쓰기권한 정합 두 경로"로 확장 — auditdb create_all + artifacts uid 10001 alias 스왑 실측. §0 갱신 예약엔 결정 7 restart 확정분도 병기(m8).

### minor (4건)

- **m5 / restart:"no" 복구 절차 미문서화** — seed 전 `up`으로 serving exit 3 정지 후 seed해도 자동 복구 안 됨. entrypoint 메시지(handoff:59)는 "seed first"만, "그 후 `docker compose up -d serving`(수동 재기동)" 없음. 정상 흐름(seed는 up 전)에선 안 걸리는 off-nominal 경로라 minor — 메시지 끝 재기동 한 줄 권장.
- **[reviser 응답]** 반영. §2.1 entrypoint 스크립트 stderr에 `docker compose up -d serving`(수동 재기동, restart:"no"라 자동 복구 안 됨) echo 한 줄 추가 + §2.5 운영노트 "seed 미완 복구 (m5)" 불릿 추가.
- **m6 / CG-1 `mem_limit≥1Gi` 단위 함정** — compose `mem_limit`은 `1g`/`1024m`/바이트 문법이고 **k8s식 `1Gi`는 유효 문법 아님**. 구현자가 `mem_limit: 1Gi`(handoff:78 문자대로)로 쓰면 `up` 거부 가능. CG-1은 "≥1Gi"를 문자 비교 말고 바이트 정규화 비교해야 하고, 핸드오프는 compose 문법 예(`1g`) 병기 안전.
- **[reviser 응답]** 반영. §2.2 serving 행에 `mem_limit: "2g"` 병기 + "compose 문법 — k8s식 `1Gi` 무효" 명기, 자원제한 불릿에 **compose 문법 주의** 서브불릿 추가(docker `RAMInBytes`는 1024 기반이라 `"1g"`=정확히 1Gi=여유 0 → reload 2배 창 여유 위해 `"2g"` 권장). §3.2 **CG-1을 "바이트로 정규화해 ≥1Gi(1073741824B) 비교, 문자 `"1Gi"` 비교 금지"로 재서술**.
- **m7 / CG-5 front-nginx `/` 검사 준-공허** — 모든 healthcheck URL이 `/` 포함이라 front-nginx "엔드포인트 `/`" 파싱은 사실상 항상 통과(가드 미미). console-web은 `8080` 포트 검사가 실질 가드라 유효하나 front-nginx엔 포트 검사 없어 회귀 방지력 약함(비-게이트라 파급 없음).
- **[reviser 응답]** 반영. §3.2 CG-5 front-nginx 서브불릿에 **포트 80 검사 추가**(실질 가드)하고 "`/` 준-공허·front-nginx는 비-게이트라 파급 없음"을 명시 주석.
- **m8 / decisions.md:153 문구 잔류** — 결정 7 미결이 아직 `restart(unless-stopped)`·"POC는 restart만"으로 서술. 핸드오프가 restart:"no"로 종결·권위이나 §0 갱신 예약은 M2-2만 걸고 restart 해소분은 안 걸었음. 통과 후 reviser가 결정 7에 restart:"no" 확정도 함께 반영 권장.
- **[reviser 응답]** 반영. §0 "decisions.md 반영 예약"을 재작성 — (a) M2-2 entrypoint 대체 + **(b) 결정 7 미결(decisions.md:153) `restart(unless-stopped)`→`restart: "no"` 확정** 두 갱신을 함께 예약. handoff.md 안에서 예약만; decisions.md는 이번에 미수정(통과 후 별도).

---

**blocker: 1건** (B2-R2). blocker≠0 → **HOLD**. R1의 B1·M1·M2는 실물 대조로 **정확히 해소**(가짜 수렴 아님). 남은 blocker는 v2가 새로 만든 게 아니라 **R1이 못 본 층** — §2.5 chown 10001이 `user:` 결정과 짝지어지지 않아 내부 모순이고, 설계(결정 2·6)가 "핸드오프에서 반드시 확정"하라 위임한 artifacts 쓰기권한 정합이 통째로 누락돼 임계 쓰기 경로(alias 스왑=번들 원자성 불변식)를 위협. reviser가 (1) `user:` 확정, (2) 비-root면 artifacts host-chown 이관, (3) §2.5 chown 정합으로 해소해야 진행 가능.
