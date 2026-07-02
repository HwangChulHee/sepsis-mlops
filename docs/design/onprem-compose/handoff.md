# On-Prem Compose 통합 — 구현 핸드오프 (spec-writer / 구현자용)

> **입력**: `docs/design/onprem-compose/decisions.md` (review-loop 4R 통과, blocker 0). **이 핸드오프가 설계→구현 번역의 권위**이며, 충돌 시 실물 코드가 최종 판정.
> **목적**: 결정 1~9를 "무엇을 어느 파일에 만드는가"로 번역한다. spec-writer는 **이 문서만 보고** TDD RED를 쓴다(src/ 미열람 원칙 유지). 단 이 핸드오프는 src/ 실물 시그니처·경로를 이미 대조해 기입했다.
> **출처등급**: `[확인됨]`(코드 대조) · `[핸드오프 결정]`(설계를 구현으로 좁힌 선택) · `[검증 필요]`(런타임 실측=스모크).
> **상태**: 핸드오프 v4 — 핸드오프 검토 R3 반영(PASS, major·minor 하드닝). **구현·SM 실측 완료** — 리포트 `docs/reports/onprem_compose_smoke.md`. ★실측 정정: 아래 healthcheck의 `localhost`는 `::1`(IPv6) 우선 해석 함정이라 **실제 구현은 `127.0.0.1`**로 고정(busybox wget IPv6 폴백 부재). 회귀 가드 CG-11 추가(`tests/deploy/test_compose_contract.py`).

### 개정 로그
- **v4 (R3 반영, 2026-07-02)** — R3은 **blocker 0 = PASS**. 남은 major 1·minor 1을 spec-writer RED 완성·운영노트 정확화 목적으로 하드닝(통과 저지 아님).
  - **major (CG-10 신설)**: §2.2가 console-api env `CONSOLE_AUDIT_DB_URL=sqlite:////app/auditdb/console_audit.db`를 처방하나 CG-1~9 어디에도 이 env 존재를 강제하는 계약이 없었다(CG-3=`SERVE_URL` localhost 금지와 **동일 클래스인데 감사 쪽만 무가드**). env 누락 시 `service.py:30`이 `config.py:27-30` 기본 `sqlite:///{VAR_DIR}/console_audit.db`(=`/app/var`)로 폴백 → `audit_uri()`가 `VAR_DIR.mkdir()`(=`/app/var`)를 시도 → **`/app`은 root 소유라 uid 10001이 생성 불가 → `audit.py:56 create_all` 직전 permission denied → import 단계 부팅 크래시** [확인됨: `config.py:12` `ROOT=parents[2]`(=컨테이너 `/app`)·`:18` `VAR_DIR=ROOT/"var"`·`:27-30` `mkdir`+폴백 URI, `service.py:30` `os.environ.get("CONSOLE_AUDIT_DB_URL") or C.audit_uri()`, `audit.py:54-56` `create_all`]. **root(v2 이전)라면 `/app/var`가 생성돼 "비영속"에 그쳤을 문제를, B2-R2의 uid 10001 전환이 "부팅 크래시"로 승격**시켰다 — 그래서 R3에서 major로 부상. **CG-10 신설**(§3.2): console-api env에 `CONSOLE_AUDIT_DB_URL`이 존재하고 `sqlite:////app/auditdb/`(auditdb named volume 경로)를 가리킴을 정적 파싱 강제(=`/app/var` 기본 폴백 금지). CG-3와 대칭 구조. 값은 §2.2에 이미 정확해 spec-준수 구현은 정상 동작 → **회귀 가드 공백 메움**.
  - **minor (host-chown 특권 명시)**: §2.2·§2.5 `chown -R 10001:10001 ./deploy/artifacts`는 **다른 uid로의 소유권 변경이라 CAP_CHOWN(root/sudo) 필요** — 호스트 개발자가 uid≠0(예: 1000)이면 "Operation not permitted"로 실패. 대안 "seed를 uid 10001로 실행"도 특권 필요. §2.5 운영노트에 "host-chown·seed-as-10001은 호스트 uid≠0일 때 sudo 필요" 한 줄 추가.
- **v3 (R2 반영, 2026-07-02)**
  - **B2-R2 (blocker)**: §2.5 `chown 10001`이 `user:`/USER 없이 죽은 처방이던 내부 모순을 해소하고, decisions.md 결정 2(:69)·결정 6(:125)이 "핸드오프에서 반드시 확정"하라 위임한 **artifacts bind mount 쓰기권한 정합**을 확정 기입. 실물 확인: `deploy/Dockerfile`·`Dockerfile.api` 둘 다 **USER 지시어 없음(=root 기본)**, `console-web/Dockerfile`만 `USER 101`(비-root, artifacts·auditdb 미접근) [확인됨]. 설계가 이미 uid 10001로 chown을 처방했으므로(결정 2 M-3·결정 5 B2-2) **POC 스탠스를 "비-root `user: "10001:10001"`"로 확정**(K8s `runAsUser 10001` 정합) — (1) §2.2 serving·console-api에 `user: "10001:10001"` 명시, (2) 승인 시 alias 스왑(`bundle.py set_alias`→`os.replace`)이 host-owned 폴더에서 permission denied 나지 않도록 **artifacts 호스트측 소유권 정합**(seed 후 `chown -R 10001:10001 ./deploy/artifacts` 또는 seed를 uid 10001로) 를 §2.2 volumes·§2.5 운영노트에 이관, (3) §2.5 auditdb chown 10001 **존치**(이제 `user: "10001"`과 정합). **CG-9 신설**(§3.2)로 serving·console-api `user: "10001..."`를 정적 파싱 강제 → 번들 원자성·감사 append-only 두 불변식 가드.
  - **minor**: m5(§2.1 entrypoint 메시지·§2.5에 "seed 후 `docker compose up -d serving` 수동 재기동" 복구 한 줄 — restart:"no"라 자동 복구 안 됨), m6(§2.2 `mem_limit`에 compose 문법 예 `"1g"` 병기 + CG-1을 "≥1Gi 문자 비교"가 아니라 **바이트 정규화 비교**로 서술 — k8s식 `1Gi`는 compose 유효 문법 아님), m7(CG-5 front-nginx에 포트 `80` 검사 추가 + `/` 준-공허·비-게이트 주석), m8(§0 "decisions.md 갱신 예약"에 M2-2뿐 아니라 **restart:"no" 확정분(결정 7)**도 예약 — decisions.md는 통과 후 별도 반영).
- **v2 (R1 반영, 2026-07-02)**
  - **B1 (blocker)**: serving restart 정책을 **`restart: "no"` 단일 확정**. "unless-stopped+종료코드 무시" 서술 삭제 — Docker restart 정책엔 종료코드별 억제 기능이 없어 `unless-stopped`는 exit 3에도 항상 재시작(무한 crash-loop) → §0 "읽을 수 있는 실패"가 붕괴하기 때문. "실크래시 auto-restart ⊥ precondition no-crash-loop" 동시 불가를 트레이드오프로 명시(§2.1). **CG-8 신설**(§3.2)로 serving restart가 `always`/`unless-stopped`/`on-failure`가 아님을 파싱 강제 → SM-6 이전 RED로 포착.
  - **M1 (major)**: exec-form ENTRYPOINT의 shebang·실행권한 공백을 **`ENTRYPOINT ["/bin/sh","/app/deploy/serving-entrypoint.sh"]`로 확정**(기존 CMD도 `/bin/sh -c`라 최소변경) [확인됨: `deploy/Dockerfile:36`]. 스크립트 첫 줄 `#!/bin/sh`도 병기. §3.1에 "`/bin/sh script`로 부르면 shebang 없이도 GREEN이나 exec-form이면 컨테이너 실패"라는 번역 괴리 명시.
  - **M2 (major)**: CG-5를 확장해 console-web healthcheck가 `8080`/`/`, front-nginx가 `/`를 치는지도 파싱 강제(§2.2 표 값 근거). 정적 파싱이 명령 바이너리 존재까지는 못 봄을 SM 한계로 명시.
  - **minor**: m1(§0 한계를 "번들 내부 파일 손상" sub-case로 좁힘 — `-e`는 dangling 심링크를 부재로 잡음), m2(serving healthcheck `start_period`/`timeout` 명시), m3(console-api healthcheck 인자 `?fs=vitals` 리터럴 전달=셸 미경유 §3 주석), m4(§2 구현참조 / §3 계약 경계 한 줄).
- **v1**: 초안 — decisions.md(4R 통과) → 구현 번역.

---

## 0. 핸드오프의 핵심 판단 — M2-2를 (B) entrypoint로 대체 (app.py 불변)

decisions.md 결정 7은 "seed 미완 시 읽을 수 있는 실패"를 위해 **app.py에 lifespan 훅 신규 추가(M2-2)**를 의존으로 기록했고, 이는 "serving 코드 재사용, 배관만" 스코프를 넘는다고 정직하게 명시했다 [확인됨: decisions.md 결정 7 M2-2, `app.py:31` lifespan 미지정].

**핸드오프 결정: app.py를 건드리지 않고, seed precondition을 컨테이너 entrypoint 셸에서 확인한다** — 이로써 M2-2의 app.py 변경을 **불필요화**한다.

- **근거**: 번들 유무는 파일시스템 사실(전제조건)이지 추론 로직이 아니다 → 배관(entrypoint) 관심사. serving 파이썬 코드 0줄 변경으로 "배관만" 스코프를 지킨다 [핸드오프 결정].
- **방식**: `exec uvicorn` 직전에 활성 alias 존재를 확인, 없으면 명확 메시지 + 비재시작 종료. alias 이름은 `gru_<SERVE_FEATURESET>` = **`gru_vitals`** [확인됨: `app.py:45-46` `_resolve_alias`가 `ARTIFACTS/f"gru_{fs}"` 해석; 결정 8 fs=vitals].
- **한계(정직, m1 정정)**: entrypoint는 `[ -e ]`로 alias **존재**를 본다. `-e`는 심링크를 따라가므로 **dangling 심링크(대상 dir 삭제)까지 부재로 판정→exit 3**로 잡힌다 — §0 대체는 "seed 미완"뿐 아니라 "alias가 죽은 링크"도 읽을 수 있는 실패로 만든다. **남는 한계는 좁다**: alias·대상 dir은 실재하되 **번들 내부 파일이 손상된 경우(예: dir 있고 `model.pt`가 깨짐)**만 app.py lazy 로드에서 여전히 stuck-unhealthy. 이 sub-case는 스모크 SM-6 주석으로 남긴다 [핸드오프 결정].
- **decisions.md 반영 예약 (통과 후 별도 — m8)**: 이 대체를 결정 7에 반영하려면 문서 개정(v최종+1)이 필요 — 핸드오프 검토 통과 후 reviser가 결정 7의 **두 미결**을 갱신한다: (a) M2-2를 "entrypoint 대체로 해소"로, (b) 결정 7 미결/옵션(decisions.md:153)의 `restart(unless-stopped)`·"POC는 restart만" 서술을 **`restart: "no"` 확정**(비재시작 종료코드 구분은 Docker 불가 → restart를 끄는 것으로 해소, §2.1 B1)으로. **본 핸드오프가 두 갱신의 근거.** (decisions.md는 이번 핸드오프 수정에서 건드리지 않는다 — 예약만.)

> 면접 한 문장: *"seed 전제조건 체크를 앱이 아니라 entrypoint에 둬서, 리뷰 루프가 스코프 침범으로 지목한 app.py 변경(M2-2)을 아예 없앴다 — 전제조건은 배관의 관심사라 serving 코드를 0줄 건드렸다."*

---

## 1. 산출물 3층 분류 (검증 방식이 층마다 다름)

(가)는 코드 로직이 거의 없고 **설정+런타임**이 본체다. 따라서 검증을 3층으로 나눈다:

| 층 | 산출물 | 검증 방식 | TDD 대상? |
|---|---|---|---|
| **① 코드(최소)** | serving entrypoint 셸 (seed precondition) | pytest subprocess | ✅ RED 가능 |
| **② 설정 계약** | `docker-compose.yml`·nginx conf·prometheus.yml 수정 | pytest 파싱 계약 테스트 | ✅ RED 가능 (핵심) |
| **③ 런타임 통합** | 실제 `up` 후 거동 (심링크·cgroup·reload·전파) | 스모크 체크리스트(수동/스크립트) | ❌ 유닛 불가 |

**핵심**: ②의 "설정 계약 테스트"가 이 핸드오프의 무게중심이다 — 특히 루프 최대 교훈(healthcheck-게이트 함정, R1~R3 3연속)을 **수동 검토가 아니라 자동 회귀 가드**로 굳힌다(아래 CG-2).

---

## 2. 파일별 변경 지점 (실물 대조 기입)

### 2.1 신규 — `deploy/serving-entrypoint.sh` (① 코드)

- **역할**: seed precondition 확인 후 uvicorn 기동.
- **로직**:
  ```sh
  #!/bin/sh
  ARTIFACTS_DIR=${ARTIFACTS_DIR:-/app/deploy/artifacts}
  FS=${SERVE_FEATURESET:-vitals}
  if [ ! -e "$ARTIFACTS_DIR/gru_$FS" ]; then
    echo "FATAL: active alias 'gru_$FS' missing under $ARTIFACTS_DIR." >&2
    echo "  seed first (host): uv run python -m scripts.h4.h4s_export_bundle vitals" >&2
    echo "  then (restart:\"no\" 이므로 수동 재기동): docker compose up -d serving" >&2   # m5: 자동 복구 안 됨
    exit 3          # 종료코드 3 = seed precondition 실패 신호. restart:"no"와만 결합해야 crash-loop 없음(§2.1 종료코드 정책)
  fi
  exec uvicorn sepsis.serve.app:app --host 0.0.0.0 --port 8000 --log-level ${LOG_LEVEL:-info}
  ```
  - **첫 줄 `#!/bin/sh` 필수(M1)**: ENTRYPOINT를 exec form으로 잡으면 커널이 shebang으로 인터프리터를 찾으므로 없으면 "exec format error". 단 본 핸드오프는 아래 **§2.1 Dockerfile 변경에서 `["/bin/sh", "..."]`로 확정**하므로 shebang은 이중 안전장치(직접 실행·다른 exec form 대비).
- **실물 정합** [확인됨]: 기존 CMD가 `["/bin/sh","-c","exec uvicorn sepsis.serve.app:app --host 0.0.0.0 --port 8000 --log-level ${LOG_LEVEL:-info}"]` [확인됨: `deploy/Dockerfile:36`] — entrypoint는 **이 인자를 그대로 보존**해야 함(❗`--log-level ${LOG_LEVEL:-info}` 누락 금지). alias 경로 `ARTIFACTS/gru_vitals` [확인됨: `app.py:45-46`], seed 명령은 **`vitals` 단일 positional**(`main()`이 `sys.argv[1:] or ["vitals","vitals_labs"]`라 인자 없으면 `vitals_labs`까지 순회하다 `no h2c gru/vitals_labs run` RuntimeError 비-0) [확인됨: `scripts/h4/h4s_export_bundle.py:76-80`].
- **Dockerfile 변경 (M1 확정)**: `deploy/Dockerfile:36`의 `CMD ["/bin/sh", "-c", "exec uvicorn ..."]` → 스크립트 `COPY deploy/serving-entrypoint.sh /app/deploy/serving-entrypoint.sh` + **`ENTRYPOINT ["/bin/sh", "/app/deploy/serving-entrypoint.sh"]`**. **exec form에 스크립트 경로만 넣지 말 것** — 그러면 커널이 스크립트를 직접 exec하므로 shebang·실행권한(`chmod +x`)이 없으면 "exec format error"로 컨테이너 미기동. `/bin/sh`를 인터프리터로 명시하면 실행권한·shebang에 무관하게 기동하고, 기존 CMD도 `/bin/sh -c`였으므로 **최소변경**이다 [확인됨: `deploy/Dockerfile:36` `["/bin/sh","-c",...]`]. (스크립트 첫 줄 `#!/bin/sh`도 병기하되, 기동 보장은 이 ENTRYPOINT 형태가 담당.) **src/ 는 불변** [핸드오프 결정].
- **종료코드 정책 (B1 확정)**: precondition 실패 = `exit 3`. Compose serving restart는 **`restart: "no"` 단일값으로 확정**한다 [핸드오프 결정].
  - **왜 단일값인가**: Docker/Compose restart 정책에는 **종료코드별 재시작 억제 기능이 없다**(그건 systemd `RestartPreventExitStatus`/K8s 개념). `unless-stopped`·`always`는 종료코드 무관 **항상** 재시작 → exit 3에도 재시작 → 무한 crash-loop → §0이 노린 "읽을 수 있는 실패(seed 미완=명확 정지)"가 붕괴한다. `on-failure`도 비-0 종료를 재시작하므로 부적격. 따라서 `"no"`만 §0을 지킨다.
  - **트레이드오프(정직)**: "serving 실크래시(비-precondition) auto-restart"와 "precondition exit 3 no-crash-loop"는 **Docker에서 동시 불가**하다(둘 다 종료코드로 갈라야 하는데 Docker는 못 함). **POC는 전자(auto-restart)를 포기**하고 후자(읽을 수 있는 실패)를 택한다 — seed 미완 진단성이 POC의 지배 목표이기 때문. auto-restart가 필요하면 K8s(`restartPolicy`+`livenessProbe`) 또는 systemd 래퍼로 승격(로드맵) [핸드오프 결정].
  - decisions.md 결정 7 미결(비재시작 종료코드 구분)은 "Docker에선 restart 정책이 아니라 restart를 끄는 것으로 해소"로 확정 [핸드오프 결정].

### 2.2 신규 — `deploy/docker-compose.yml` (② 설정)

통합 스택. 기존 `deploy/monitoring/docker-compose.yml`(모니터링 전용)을 **흡수**하거나 include. 서비스·핵심 설정(decisions.md 서비스 맵·결정 3·4·7·9 기준):

| 서비스 | build/image | 포트 | 핵심 |
|---|---|---|---|
| serving | `build: {context: ., dockerfile: deploy/Dockerfile}` | `8000:8000` 호스트 공개 | env `SERVE_FEATURESET=vitals`, **`cpus`·`mem_limit: "2g"`**(결정 9; ≥1Gi + reload 2배 여유. **compose 문법 — k8s식 `1Gi` 무효**, m6), BLAS 스레드캡 env, healthcheck=python urllib `/health`(+**`start_period`≥캘리브레이션 예산·`timeout`**, m2), **`restart: "no"`**(B1 — exit 3 crash-loop 방지, §2.1), **`user: "10001:10001"`**(B2-R2 — 비-root, artifacts 읽기·K8s runAsUser 정합) |
| console-api | `build: {context: ., dockerfile: deploy/k8s/console/Dockerfile.api}` | 내부 8000 | env **`SERVE_URL=http://serving:8000`**·`CONSOLE_AUDIT_DB_URL=sqlite:////app/auditdb/console_audit.db`·`CONSOLE_FEATURESETS=vitals`, healthcheck=**wget `/console/versions?fs=vitals`**(exec-form 인자라 `?fs=vitals` **리터럴 전달=셸 미경유**, m3/§3.2), `depends_on: serving:{condition: service_started}`, **`user: "10001:10001"`**(B2-R2 — alias 스왑·auditdb 쓰기 uid) |
| console-web | `build: console-web/` | 내부 8080 | healthcheck=wget `--spider` `http://localhost:8080/` (B3-1 신규) |
| front-nginx | `build: deploy/nginx/` (alpine nginx) | `80:80` 호스트 공개 | `depends_on: {console-api:{service_healthy}, console-web:{service_healthy}}`, healthcheck=wget `/` |
| prometheus | 기존 이미지 | 9090 | 타깃 `serving:8000` |
| grafana / renderer | 기존 | 3000 / 내부 | 그대로 |

- **build context 주의** [확인됨]: `Dockerfile.api`는 `COPY src/` 하므로 context=**repo 루트**(`.`), dockerfile 경로만 지정 [확인됨: `deploy/k8s/console/Dockerfile.api`가 src/ 복사].
- **자원 제한** [확인됨: 결정 9]: 반드시 **최상위 `cpus:`/`mem_limit:`**, **`deploy.resources` 금지**(v1에서 무시됨). `cpus`=BLAS 스레드캡과 동수, `mem_limit`은 reload 2배 창을 수용해 **1GiB 이상**.
  - **compose 문법 주의 (m6)**: `mem_limit`은 **compose 문법**(`"1g"`/`"1024m"`/바이트 정수)만 받고 **k8s식 `"1Gi"`는 유효 문법이 아니다** — `1Gi`라 쓰면 `docker compose up`이 거부할 수 있다. compose(docker `RAMInBytes`)는 `g`/`m`을 **1024 기반**으로 파싱하므로 `"1g"`=1073741824B=**정확히 1Gi**(최소선 충족, 여유 0). reload 2배 창 여유를 위해 `"2g"` 권장. CG-1은 이 값을 **바이트로 정규화해 ≥1Gi(1073741824B) 비교**한다(§3.2) [핸드오프 결정].
- **volumes**: `artifacts`=**bind mount** `./deploy/artifacts:/app/deploy/artifacts`(serving·console-api 공유), `auditdb`=named volume `/app/auditdb`(console-api 전용).
- **★비-root 실행 + 쓰기권한 정합 (B2-R2 — 결정 2:69·결정 6:125 위임 확정)** [확인됨 + 핸드오프 결정]: serving·console-api는 **비-root `user: "10001:10001"`**로 실행한다 — K8s `runAsUser 10001`·`fsGroup 10001`과 정합하고(`console-api.yaml:33,37,53`), 결정 2 M-3·결정 5 B2-2가 이미 처방한 `chown 10001`을 유효하게 만든다. **두 Dockerfile 모두 USER 지시어가 없어 기본 root**이므로 [확인됨: `deploy/Dockerfile`·`Dockerfile.api` USER 부재] 이 `user:`가 유일한 uid 확정 지점이다. Compose엔 fsGroup 재귀 chown 장치가 없으므로 **임계 쓰기 두 경로**의 소유권을 uid 10001로 맞춰야 한다:
  - **artifacts (bind mount, host chown 필수 — 결정 2:69)**: 승인 시 alias 스왑(`bundle.py set_alias`→`os.replace`)을 console-api(uid 10001)가 host-owned 폴더에서 시도 → 소유권이 어긋나면 **permission denied → 번들 원자성 불변식 붕괴**. seed(`h4s_export_bundle`)는 호스트에서 도는 선작업이라 파일이 호스트 개발자 uid로 생성되므로, **seed 직후 `chown -R 10001:10001 ./deploy/artifacts`**(또는 seed 자체를 uid 10001로 실행)해 컨테이너 uid와 정합시킨다 → §2.5 운영노트에도 명기. [검증 필요: WSL2 bind mount uid 매핑]
  - **auditdb (named volume)**: §2.5의 `Dockerfile.api` `chown 10001`이 named volume 최초 마운트 소유권을 uid 10001로 상속시킨다 — 이제 `user: "10001"`과 정합해 `AuditStore` `create_all`이 permission denied 없이 성공(감사 append-only 불변식 보존).
  - **console-web은 예외**: base(`nginxinc/nginx-unprivileged`)가 이미 `USER 101`이고 artifacts·auditdb를 건드리지 않으므로 uid 10001 정합 대상이 아니다 [확인됨: `console-web/Dockerfile:20`].
  - **정적 파싱 가드**: `user:` 존재는 **CG-9**(§3.2)로 강제 — host chown·실제 uid 매핑은 SM-5/SM-7(런타임)의 몫.
- **console-api 부팅 순서** [확인됨: 결정 7 M-2]: serving에 `service_started`로만(❗`service_healthy` 아님 — 캘리브레이션 300s·seed 누락 진단성).
- **serving healthcheck 타이밍 (m2)** [확인됨: 결정 7 "start_period로 커버"]: `/health` 첫 프로브가 300-trial lazy 캘리브레이션을 트리거하므로 `start_period`를 **캘리브레이션 예산(≈300s) 이상**으로, `timeout`은 그보다 짧게(예: 5s) 명시한다 — start_period 창 동안의 실패는 unhealthy로 세지 않으므로 부팅 중 오탐이 없다. serving을 `service_healthy`로 게이트하는 다운스트림이 없어(console-api=`service_started`) 파급은 없으나 값은 명시한다 [핸드오프 결정].

### 2.3 신규 — `deploy/nginx/default.conf` + Dockerfile (② 설정)

- 라우팅만: `location /console/ { proxy_pass http://console-api:8000; }` / `location / { proxy_pass http://console-web:8080; }` [확인됨: 결정 3, `ingress.yaml` 라우팅].
- base=alpine nginx(busybox wget 가용, healthcheck용) [확인됨: 결정 7 M2-1 front-nginx=alpine 결정].

### 2.4 수정 — `deploy/monitoring/prometheus.yml` (② 설정)

- 타깃 `host.docker.internal:8000` → **`serving:8000`** [확인됨: 결정 4, 현행 파일].

### 2.5 수정 — `deploy/k8s/console/Dockerfile.api` (② 설정, auditdb 소유권) + 운영노트

- `/app/auditdb` 를 이미지에서 **`mkdir + chown 10001`** 선생성 → named volume 최초 마운트 시 uid 10001 소유권 상속(B2-2 크래시 방지) [확인됨: 결정 5·mentor-brief §3, `AuditStore` import 시 create_all]. **존치 확정(B2-R2)**: §2.2가 console-api를 `user: "10001:10001"`로 실행하므로 이 chown이 이제 유효하다(root 실행이면 죽은 처방이었음). 현재 `Dockerfile.api:25`는 `mkdir -p /app/deploy/artifacts`만 하고 `/app/auditdb`·chown이 없으므로 [확인됨] 이 줄을 추가한다.
- **운영노트 (B2-R2·m5)**:
  - **artifacts 호스트 소유권 정합 (필수 — 결정 2:69)**: seed 후 `chown -R 10001:10001 ./deploy/artifacts`(또는 seed를 uid 10001로 실행). 안 하면 승인 시 alias 스왑이 permission denied → 번들 원자성 붕괴. §2.2 ★비-root 불릿 참조.
    - **특권 필요 (R3 minor)**: 이 `chown -R 10001:10001`은 **다른 uid로의 소유권 변경이라 CAP_CHOWN(root/sudo)이 필요**하다 — 호스트 개발자가 uid≠0(예: 1000)이면 `sudo` 없이는 "Operation not permitted"로 실패한다. 대안 "seed를 uid 10001로 실행"(예: `sudo -u '#10001'` 또는 컨테이너 내 seed)도 특권이 필요하다. 즉 **host-chown·seed-as-10001은 호스트 uid≠0일 때 sudo 필요**. [검증 필요: WSL2 sudo·uid 매핑]
  - **seed 미완 복구 (m5)**: `restart: "no"`라 seed 전 `up`으로 serving이 exit 3 정지하면 **자동 복구되지 않는다** → seed 후 `docker compose up -d serving`으로 **수동 재기동**(entrypoint 메시지에도 병기, §2.1).
  - 기존 root 소유 auditdb volume은 재배포 시 `docker volume rm` 선행 [확인됨: 알려진 한계].

---

## 3. TDD RED 대상 (spec-writer 작성)

> **§2 vs §3 경계 (m4)**: §2는 **구현 참조**(어느 파일에 무슨 값 — src 라인은 "기대값 근거"일 뿐)이고, §3은 **계약**(spec-writer가 RED로 옮길 검증 규칙)이다. 테스트 대상 산출물(compose.yml·entrypoint.sh·nginx conf)은 전부 신규라 §2의 src 라인 인용은 해답 누수가 아니다.

### 3.1 ① entrypoint 테스트 — `tests/deploy/test_serving_entrypoint.py`

- **RED**: 스크립트 없음 → 실패.
- **케이스**:
  - `gru_vitals` alias 없는 임시 ARTIFACTS_DIR로 실행 → **종료코드 3** + stderr에 "seed first" 문구.
  - alias 있는 경우 → uvicorn exec 시도(모의: uvicorn을 스텁으로 치환해 exec 도달 확인, 또는 `--help` dry).
- subprocess로 스크립트 호출(파이썬 로직 아님).
- **번역 괴리 주의 (M1)**: 테스트가 스크립트를 `/bin/sh path/to/entrypoint.sh`로 부르면 shebang·실행권한과 무관하게 통과한다. 하지만 컨테이너 기동은 §2.1 ENTRYPOINT 형태에 달렸다 — 그래서 실제 기동 보장은 이 유닛이 아니라 **CG(§3.2)로 ENTRYPOINT가 `/bin/sh`를 인터프리터로 두는지 파싱**하거나 SM-6(런타임)에서 확인한다. 유닛 GREEN이 컨테이너 기동을 증명하지 않음을 명시.

### 3.2 ② 설정 계약 테스트 — `tests/deploy/test_compose_contract.py` (★핵심)

`deploy/docker-compose.yml`·`prometheus.yml`을 파싱(yaml)해 계약을 강제. **RED**: 파일 없음/규칙 위반 → 실패.

- **CG-1 (자원 함정, B-R0-1; m6 단위 정규화)**: 어떤 서비스도 `deploy.resources`를 갖지 않는다. serving은 최상위 `cpus`·`mem_limit` 보유. `mem_limit`은 **compose 단위 문자열(`"1g"`/`"1024m"`/바이트 정수)을 바이트로 정규화한 뒤 ≥1Gi(1073741824B)로 비교**한다 — `"1Gi"` 같은 k8s 문법은 compose 무효라 그 자체로 실패 처리(테스트는 문자 `"1Gi"` 비교 금지, 바이트 파싱 후 비교).
- **CG-2 (★healthcheck 전수 게이트, 루프 최대 교훈)**: **`depends_on`에서 `condition: service_healthy`로 참조되는 모든 서비스는 자신의 `healthcheck`를 반드시 정의**한다. (R1~R3 3연속 함정을 한 규칙으로 봉인 — 서비스 단위가 아니라 규칙 단위 전수.)
- **CG-3 (배선, 결정 3)**: console-api env에 `SERVE_URL=http://serving:8000` 존재(localhost 기본 금지).
- **CG-4 (featureset 정합, B-R0-4)**: `SERVE_FEATURESET`와 `CONSOLE_FEATURESETS`가 모두 `vitals`.
- **CG-5 (healthcheck 엔드포인트·포트 정합, B2-1·B3-1, M2 확장)**: healthcheck **존재**만이 아니라 **정확성**을 파싱 강제한다 — 잘못된 포트/엔드포인트는 영구 unhealthy(B3-1 실제 함정)라 CG-2로는 안 걸리기 때문.
  - console-api healthcheck가 `/health` 아님·**`/console/versions?fs=vitals`** 사용(§2.2 값).
  - serving healthcheck가 **`/health`**·포트 **8000**.
  - **console-web** healthcheck가 **포트 8080**·엔드포인트 **`/`**(§2.2 표 값 — B3-1 신규).
  - **front-nginx** healthcheck가 엔드포인트 **`/`**·포트 **80**. **주의(m7)**: 모든 healthcheck URL이 `/`를 포함하므로 front-nginx의 `/` 파싱은 사실상 항상 통과(준-공허) — 그래서 실질 가드로 **포트 80**을 함께 검사한다. 단 front-nginx는 기동 게이트의 뒤끝(비-게이트, decisions.md:143)이라 이 CG가 약해도 붕괴 파급은 없다.
  - **한계 명시(SM)**: 정적 YAML 파싱은 문자열(포트·경로)만 본다 — 명령 바이너리(`wget`/`python`)가 이미지에 실재하는지·실제로 200을 받는지는 못 본다. 그건 SM-4/SM-6(런타임)의 몫 [핸드오프 결정].
- **CG-6 (모니터링, 결정 4)**: prometheus 타깃 = `serving:8000`(`host.docker.internal` 금지).
- **CG-7 (순서, M-2)**: console-api→serving 의존이 `service_healthy` 아님(`service_started` 또는 부재).
- **CG-8 (serving restart 시맨틱, B1 — §0 지배 산출물 가드)**: serving 서비스의 `restart`가 **`always`·`unless-stopped`·`on-failure`가 아니다**(=`"no"`이거나 부재). Docker restart 정책엔 종료코드별 억제가 없어 이들 중 하나면 exit 3에도 재시작→crash-loop→§0 붕괴하므로, **SM-6(런타임) 이전에 RED로 잡히도록** 정적 파싱으로 강제한다. (부재도 Compose 기본이 `"no"`라 허용.)
- **CG-9 (비-root uid 정합, B2-R2 — 번들 원자성·감사 append-only 가드)**: **serving·console-api 두 서비스가 `user:`를 명시하고 그 uid가 `10001`**이다(예: `"10001"` 또는 `"10001:10001"`). 두 Dockerfile에 USER가 없어 기본 root이므로 [확인됨: `deploy/Dockerfile`·`Dockerfile.api` USER 부재], `user:`가 없으면 root 실행 → §2.5 `chown 10001`이 죽은 처방이 되고 결정 6(비-root) 위반. 비-root uid 10001이 확정돼야 artifacts host-chown·auditdb chown 전제가 성립해 alias 스왑(번들 원자성)·`create_all`(감사 append-only)이 permission denied 없이 돈다. (호스트측 소유권 정합·실제 uid 매핑은 정적 파싱 밖 → SM-5/SM-7. console-web은 base가 이미 uid 101이라 이 CG 대상 아님.)
- **CG-10 (감사 DB env 가드, R3 major — 감사 append-only·부팅 안전 가드; CG-3와 대칭)**: **console-api env에 `CONSOLE_AUDIT_DB_URL`이 존재하고 그 값이 `sqlite:////app/auditdb/`**(§2.5 auditdb named volume 경로)**를 가리킨다** — `/app/var`로 떨어지는 기본 폴백 금지. env가 없으면 `service.py:30`이 `config.py:27-30` 기본 `audit_uri()`로 폴백해 `VAR_DIR.mkdir()`(=`/app/var`)를 시도하는데, **B2-R2의 uid 10001 전환 후엔 `/app`이 root 소유라 mkdir 불가 → `create_all` permission denied → import 단계 부팅 크래시**로 승격한다 [확인됨: `config.py:12,18,27-30`·`service.py:30`·`audit.py:54-56`]. CG-3(`SERVE_URL` localhost 기본 폴백 금지)와 **동일 클래스의 env-폴백 함정**인데 R2까지 감사 쪽만 무가드였다. 값은 §2.2에 정확히 명시돼 spec-준수 구현은 정상이므로 blocker가 아닌 **회귀 가드**로 정적 파싱 강제한다. (env가 실제로 그 경로에 쓰기 가능한지·named volume 소유권 정합은 정적 파싱 밖 → SM-5.)

> CG-2가 이 세트의 심장 — 리뷰 루프가 4R에 걸쳐 사람이 잡은 걸 **파싱 규칙 1개로 회귀 방지**한다. **CG-8은 §0(읽을 수 있는 실패)을 지키는 필수 가드** — 이게 없으면 구현자가 `unless-stopped`를 골라 §0 지배 산출물이 런타임에서야 crash-loop으로 붕괴한다. **CG-9는 번들 원자성·감사 append-only 두 불변식의 정적 가드** — `user:` 없이 root로 돌면 §2.5 chown이 무효화돼 임계 쓰기 경로가 조용히 깨진다. **CG-9·CG-10은 짝** — CG-9가 uid 10001을 확정하는 순간 감사 DB 경로 폴백(`/app/var`)이 "비영속"에서 "부팅 크래시"로 승격하므로, CG-10이 env를 강제해 그 크래시를 정적 단계에서 봉인한다(둘 다 CG-3와 같은 env-폴백 가드 계열).

> 면접 한 문장: *"리뷰에서 4라운드 걸려 잡은 healthcheck-게이트 함정을, compose를 파싱해 '`service_healthy`로 게이트되는 서비스는 healthcheck가 있어야 한다'는 계약 테스트 한 개로 자동화했다 — 사람이 전수로 훑던 걸 CI가 대신한다."*

---

## 4. 스모크 체크리스트 (③ 런타임 — 유닛 불가, `up` 후 실측)

decisions.md의 `[검증 필요]`를 실행 항목으로 전수 이관. **TDD 아님** — `docker compose up` 후 스크립트/수동 확인:

- **SM-1 (심링크, 결정 2)**: `docker exec serving readlink -f /app/deploy/artifacts/gru_vitals` → 버전 dir로 해석되는지(WSL2 bind mount 경계 넘는지) [검증 필요].
- **SM-2 (자원, 결정 9)**: `docker exec serving cat /sys/fs/cgroup/.../cpu.max`·`memory.max` 또는 `docker stats` → `cpus`·`mem_limit` 실적용 확인 [검증 필요].
- **SM-3 (reload OOM, 결정 9)**: 부하 중 `/admin/reload` 트리거 → 메모리 2배 창이 `mem_limit` 안인지, OOM-kill 없는지 [검증 필요].
- **SM-4 (console-api 빈상태 200, B2-1)**: alpine console-api에서 `/console/versions?fs=vitals`가 번들 0개에서도 200 [검증 필요].
- **SM-5 (쓰기권한 정합 두 경로, B2-2·B2-R2)**: (a) fresh auditdb volume에서 uid 10001로 `create_all` 성공(부팅 크래시 없음), (b) **artifacts bind mount**에서 uid 10001(console-api)이 alias 스왑(`os.replace`)을 permission denied 없이 수행 — seed 후 `chown -R 10001:10001 ./deploy/artifacts`가 컨테이너 uid와 정합하는지(WSL2 uid 매핑 포함) 실측 [검증 필요].
- **SM-6 (seed 미완 실패, B-R0-3)**: 빈 artifacts로 `up` → serving이 **종료코드 3 + 명확 메시지**(stuck-unhealthy 아님). (alias 있지만 번들 손상 케이스는 여전히 stuck-unhealthy — entrypoint 한계, §0.) [검증 필요].
- **SM-7 (전파 E2E, 종합검토 1)**: console 승인 → alias swap → serving `/admin/reload` → 새 버전 서빙(핫스왑 무중단). bind mount 경계로 전파 사슬 보존 [검증 필요].

---

## 5. 스코프 밖 (구현하지 말 것)

- 부하테스트(나) — Locust 시나리오·매트릭스는 별도 핸드오프.
- serving **파이썬 코드 변경** — entrypoint(§0)로 대체했으므로 app.py 불변 유지. (변경이 필요해 보이면 핸드오프 검토로 에스컬레이션.)
- 인증(SSO/OIDC)·하드닝(readOnly rootfs·seccomp) — M4·백로그.
- replica ≥2 / Redis / 무중단 코드배포 — 로드맵.

---

## 6. 핸드오프 검토 요청 (redteam read-only, 시그니처 대조)

1. **§0 entrypoint 대체가 M2-2를 온전히 대신하는가** — alias 존재 확인만으로 "지배적 seed 실패"를 잡는가, app.py 불변이 유지되는가. (**R1 해소**: 종료코드 3 + restart 조합은 `restart:"no"`로 확정, CG-8이 파싱 강제 — §2.1·§3.2 B1.)
2. **CG-2(healthcheck 전수 계약)의 파싱 규칙이 R1~R3 4서비스 함정을 모두 포괄**하는가(누락 서비스 없이).
3. **파일별 시그니처·경로 실물 정합**: `app.py:45-46` alias 규칙, `Dockerfile.api` build context=루트, `console/api.py` 라우트 목록(`/health` 부재), prometheus 타깃 — 핸드오프 기입이 src/와 일치하는가.
4. **entrypoint `exec uvicorn` 인자**가 기존 CMD와 동일한가 → **핸드오프에서 확인 완료**: `deploy/Dockerfile:36` CMD에 `--log-level ${LOG_LEVEL:-info}` 포함, entrypoint에 반영함(§2.1). 재검증만.
5. ~~seed 명령 `h4s_export_bundle vitals` 인자 형태~~ → **핸드오프에서 확인 완료**: `main()`이 `sys.argv[1:] or ["vitals","vitals_labs"]` [확인됨: `scripts/h4/h4s_export_bundle.py:76-80`] → `vitals` positional 정확.