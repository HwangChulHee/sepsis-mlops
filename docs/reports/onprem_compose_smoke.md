# On-Prem Compose 스모크 Findings — 런타임 실측 (가)

> **목표: 계약이 아니라 동작.** `docs/design/onprem-compose/`의 설계(4R)·핸드오프(3R)·계약 테스트(16 GREEN)는 "설계도가 옳다"까지다. 이 리포트는 **실제 `docker compose up` 후** 종이로 확인 불가능한 런타임 층(SM-1~SM-7)을 실측한 증거다. 3층 검증(코드→계약→런타임)의 마지막 층.
> **실행 환경**: hchdesktop, WSL2 Ubuntu, Docker Compose v2. artifacts seed = `h4s_export_bundle vitals`(`gru_vitals@v0-base`).
> **판정**: SM-1·2·4·5·6·7 **PASS**, SM-3은 부하 드라이버 의존이라 **(나)로 이관**. 실측이 계약 밖 런타임 버그 **1건** 적발·수정(IPv6 healthcheck).
> **출처등급**: `[실측]`(up 후 관측) · `[우리 결정]`.

## 무엇을 쟀나

설계 내내 `[검증 필요]`로 이관했던 "돌려봐야 아는" 항목들을 실제 기동 후 확인했다 — 심링크가 컨테이너 경계를 넘는지, 자원 제한이 cgroup에 실제로 걸리는지, 모델 교체 핫스왑 전파가 끝단까지 도는지 등. 지표 *값*이 아니라 **배선·거동**만 본다.

## 결과: SMOKE PASS (SM-3 이관)

| # | 항목 | 결과 | 근거 |
|---|---|---|---|
| **SM-1** | 심링크 해석 (WSL2 bind mount) | ✅ PASS | `gru_vitals → gru_vitals@v0-base` 컨테이너 안에서 해석·읽기 성공 — 설계 내내 걱정한 WSL2 bind mount 경계 문제 해소 [실측] |
| **SM-2** | 자원 제한 실적용 | ✅ PASS | `NanoCpus=2e9`(2코어)·`Memory=2Gi` 적용, cgroup quota `200000/100000`. **결정 9의 핵심** — `deploy.resources`면 무시됐을 것을 최상위 키로 걸어 실제 cgroup에 적용됨 [실측] |
| **SM-4** | console-api 빈/채워진 상태 200 | ✅ PASS | `/console/versions?fs=vitals` → 200 JSON [실측] |
| **SM-5** | auditdb 소유권·create_all | ✅ PASS | auditdb `10001:10001` 소유, `console_audit.db` 생성 (B2-2 chown 처방 작동, 부팅 크래시 없음) [실측] |
| **SM-6** | seed 미완 → exit 3 | ✅ PASS | 빈 artifacts → `exit 3` + 읽을 수 있는 메시지 (crash-loop 아님) [실측] |
| **SM-7** | 전파 사슬 (링크별) | ✅ PASS | 쓰기권한(음성→chown 후 양성)·`/admin/reload` 핫스왑(`{"reloaded":true}`)·`SERVE_URL` 링크·front-nginx 라우팅·실추론 `p=0.467` 전부 확인 [실측] |
| 부가 | 모니터링·라우팅 | ✅ | prometheus `serving:8000` `UP=1`, front-nginx `/`→web·`/console/`→api [실측] |

**SM-3 (부하 중 reload 2배 OOM) 미실행** — 부하 드라이버가 필요해 **(나) 부하테스트 영역**. `mem_limit=2g` **적용**은 SM-2에서 확인됐고, 2배 창에서 OOM 안 나는지는 부하 하에서만 드러난다 [우리 결정].

## 🐛 실측이 잡은 버그 — IPv6 healthcheck 함정 (커밋 `6ab2c8c`)

**증상**: `localhost`가 `/etc/hosts`상 `::1`(IPv6) 우선 해석인데 uvicorn·nginx는 IPv4(`0.0.0.0`)만 리슨 → busybox `wget`이 IPv6 실패 후 **폴백 안 함** → connection refused → console-web·console-api가 `unhealthy` → front-nginx 게이트(`service_healthy`) 미충족으로 **스택 전체 미기동** [실측].

**단서**: serving은 Python `urllib`이 IPv4로 폴백해 멀쩡했고, busybox `wget`을 쓰는 console-web·console-api만 죽었다 — **같은 healthcheck라도 이미지 내장 도구 차이로 거동이 갈린 것**이 원인 추적의 열쇠였다.

**수정**: 4개 healthcheck를 `localhost` → `127.0.0.1` 명시로 교체 → 재기동 시 전 서비스 healthy.

**왜 계약이 못 잡았나**: 정적 계약(CG-5)은 문자열만 본다 — `localhost`든 `127.0.0.1`이든 포트·엔드포인트는 동일하게 들어있어 파싱으론 구별 불가. **핸드오프가 "SM은 런타임 실측, 정적 파싱 밖"이라 그은 3층 경계가 정확히 이 지점에서 값을 했다.** (회귀 방지: 이 발견은 CG-11 계약으로 승격 — `test_compose_contract.py`.)

## 정리 / 편차

- **teardown 완료**: 스택 down·볼륨 제거·artifacts 소유권 `hch:hch` 원복.
- **계약 테스트**: 16 → **17 GREEN**(CG-11 추가). IPv6 회귀 가드 굳힘.
- **3층 검증이 다 값을 했다**: 계약이 설계 함정(healthcheck-게이트 R1~R3 등)을, 런타임이 계약 밖 IPv6 함정을 각각 잡았다. **"설계도가 옳다"와 "실제로 뜬다" 사이 강을 건넜다.**
- **범위 밖(미실행)**: SM-3(부하 중 OOM), 부하테스트(나), 멀티노드/replica·Redis(로드맵).

> 면접 한 문장: *"계약 테스트 GREEN은 '설계도가 옳다'지 '뜬다'가 아니라, 실제 `docker compose up` 스모크를 별도로 돌렸다 — 그 스모크가 정적 파싱이 원리상 못 잡는 IPv6/IPv4 healthcheck 함정을 잡았고, 그 발견을 다시 계약(CG-11)으로 승격해 회귀를 막았다. 실측→정적 가드 승격이 이 3층 검증의 피드백 루프다."*