# handoff_frontend_review.md — 프론트 구현 핸드오프 B 검토

## Round 1 — redteam

- 대상: `design/console/handoff_frontend.md` (명세부 v1, 레드팀 미검토)
- 대상 commit: `3078e5d`
- 검토일: 2026-06-30
- 핵심 질문: 핸드오프 B가 (a) 실제 `/console` 백엔드 계약과 1:1 일치하는가, (b) PVC 공유 전파 사슬을 정확히 박았는가
- 판정: **FAIL — blocker 2건 / major 1건 / minor 4건**

---

## PASS

- **읽기 응답 키 1:1 일치**: `list_versions`(`service.py:200-218`) → `{featureset, active, versions:[{version, bucket, ready, gate_passed, bholdout_util, has_mlflow}]}`, `get_version_detail`(`service.py:227-245`) → `{version, bucket, ready, gate, retrain, meta{featureset,tau,trained_on}, mlflow_link}`, audit `_serialize_event`(`api.py:25-31`)의 12개 키 — 모두 핸드오프 코드현황 표(line 15-19)와 정확히 일치.
- **gate 표시 필드 실재**: `get_version_detail.gate` = `validation.json` 통째(`service.py:240`) = `dataclasses.asdict(ValidationResult)`+`validated_at`(`deploy.py:60-61`). GatePanel이 쓰는 `new_aval_util/old_aval_util/bholdout_util/no_regression/cross_site_claim/eps`가 전부 `ValidationResult` 필드(`validate.py:31-44`)로 실재. 핸드오프 line 16·54 정합.
- **propagation 값**: `_propagate_and_confirm`이 `"confirmed"`|`"pending"`만 반환(`service.py:171,173`). 핸드오프 line 22·52·132 정합. 결정 2-A-c(미전파 가시화)와도 일치.
- **전파 사슬 서빙측 실재**: serve `/admin/reload`(`app.py:136-142`)·`/health`의 `run_id`(`app.py:109`) 모두 구현됨. `_propagate_and_confirm`이 `SERVE_URL`+`/admin/reload`/`/health` 호출(`service.py:148,156,170`) — 핸드오프 line 116의 `SERVE_URL` env 이름과 일치. 전파 폴링이 실제로 닫힌다.
- **REGRESSED 422 이중 게이트**: `service.approve`→`deploy.swap`이 `no_regression=False`면 `ValueError`(`deploy.py:86-87`)→api 422(`api.py:58`). 핸드오프 M3(line 51·131)의 백엔드 422 맞물림 정합.
- **G3/G4 PromQL 실 메트릭**: `serve_predict_latency_seconds`(Histogram→`_bucket`), `serve_predict_requests_total`, `serve_alarms_total`, `serve_health_requests_total` 전부 `metrics.py:13-16`에 실재. 에러 카운터 부재도 사실(`metrics.py` 전체에 없음) → 핸드오프 line 83의 "에러율 공백" 정직.
- **환경의존 정직성**: `up{job=}`(line 93)·StorageClass·ingress addon(line 120·142)을 `[검증 필요]`로 정직 표기.

---

## blocker

### B1. 쓰기 `version` 식별자 규약이 백엔드 계약과 정반대 — 모든 approve/rollback이 422로 실패
- 문제: 핸드오프 line 55(B1)·성공기준 5(line 134)는 "쓰기 요청 `version`은 `list_versions`가 준 **순수 버전 문자열 그대로** 전달(**접두 재부착·가공 금지**)"라고 못 박는다. 그러나 백엔드는 **디렉토리명**(`gru_<fs>@<v>`)을 요구한다.
- 근거:
  - `service.approve`/`rollback`은 첫 줄에서 `_require_consistent(fs, version_id)`를 호출하고, 이 함수는 `if not version_id.startswith(f"gru_{fs}@"): raise ValueError`(`service.py:38-40,84,104`).
  - `list_versions`는 `version: _strip_prefix(fs, version_id)`로 **접두 제거된** 값("champ")만 반환(`service.py:211`). `get_version_detail`도 `_strip_prefix`로 stripped만 반환(`service.py:237`).
  - `api.py:20`의 `WriteRequest.version` 주석 = "버전 디렉토리명(gru_<fs>@<v>, B1)" — 즉 **full dir name**이 실제 계약.
  - 결론: 핸드오프대로 stripped "champ"를 보내면 `_require_consistent`가 `ValueError("version 'champ' not in featureset 'vitals'")` → api 422. 게다가 list_versions/detail 어디도 full dir name을 주지 않으므로, 프론트가 유효한 쓰기를 만들려면 **반드시 `gru_${fs}@${version}` 접두를 재부착**해야 하는데 핸드오프는 그것을 명시적으로 금지한다 → 유효한 approve/rollback 요청 구성이 **구조적으로 불가능**.
- 제안: B1을 뒤집어라 — "쓰기 요청 `version`은 `list_versions`/`detail`이 준 stripped 표면값에 `gru_<fs>@`를 재부착한 **디렉토리명**으로 전송"으로 명세. 성공기준 5도 동일 정정.

> **[reviser 응답]** 해소: B1 규약을 뒤집음 — 읽기=stripped, 쓰기=디렉토리명(`gru_${fs}@${v}`) 비대칭을 코드현황 표(`handoff_frontend.md` "읽기/쓰기 version 비대칭" 신설 bullet), 프론트 고유 계약 항목 5, 성공기준 5에 반영. API 클라이언트 스니펫에 `toDirName(fs,v)` 재부착 헬퍼(이중접두 가드 포함) 추가. 근거: `service.py:38-40,84,104`(`_require_consistent` 강제), `service.py:211,218,237`(`_strip_prefix` 응답), `api.py:20`(WriteRequest.version=dir name 주석) 직접 확인.

### B2. PVC 공유 메커니즘이 console-api에 대해 틀림 — 전파 사슬 silent 단절
- 문제: 핸드오프 line 115는 **console-api Deployment**의 환경변수로 `ARTIFACTS_DIR`를 들어 "serve와 같은 번들 저장소 공유"의 조정점으로 제시한다. 그러나 console-api 코드는 `ARTIFACTS_DIR`를 **읽지 않는다**.
- 근거:
  - console 경로: `service.ARTIFACTS = deploy.ARTIFACTS`(`service.py:25`), `deploy.ARTIFACTS = C.ROOT / "deploy" / "artifacts"` **하드코딩, env 미참조**(`deploy.py:27`). `retrain/` 전체에 `ARTIFACTS_DIR`/`os.environ` 0건(grep 확인).
  - 반면 serve는 `ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", str(C.ROOT/"deploy"/"artifacts")))`로 **env를 읽는다**(`app.py:39`).
  - 비대칭 귀결: 운영자가 핸드오프대로 양쪽에 `ARTIFACTS_DIR=/mnt/shared`를 주고 PVC를 `/mnt/shared`에 마운트하면 — serve는 `/mnt/shared`를 읽지만, **console-api는 여전히 `C.ROOT/deploy/artifacts`(예: `/app/deploy/artifacts`)를 읽어** alias swap을 거기에 쓴다. 두 컨테이너가 **다른 디렉토리**를 보게 되어 "콘솔 swap → 서빙 반영" 전파 사슬이 조용히 끊긴다(결정 2-A의 핵심 일관성 주장 붕괴).
- 근거 충돌: 결정 2(공유 자원=번들 저장소)·핸드오프 성공기준 7("console-api·serve가 **같은 artifacts PVC** 공유")의 의도는 옳으나, 명시된 *메커니즘*이 코드와 어긋난다.
- 제안: 비대칭을 명시하라. console-api는 `ARTIFACTS_DIR`를 **무시**하므로 PVC를 **코드가 읽는 고정 경로**(`<C.ROOT>/deploy/artifacts`, 컨테이너 내 실제 해석 경로 명기)에 마운트해야 한다. serve는 `ARTIFACTS_DIR`로 그 동일 마운트를 가리키게 하거나 동일 경로에 마운트한다. (대안: `deploy.py`도 `ARTIFACTS_DIR`를 읽도록 보강 — 핸드오프 A/H4r 코드 변경이라 교차단계 의존으로 표기.)

> **[reviser 응답]** 해소: K8s 절 3곳을 정정 — (1) console-api Deployment env bullet에서 "console-api는 `ARTIFACTS_DIR`를 읽지 않는다(`deploy.py:27` 하드코딩, `service.py:25`)→ PVC를 고정 경로 `<C.ROOT>/deploy/artifacts`에 마운트, serve만 `ARTIFACTS_DIR`(`app.py:39`)로 동일 경로 지정"을 명시. (2) "공유 저장소 의존"에 "비대칭 마운트 경로(B2)" 블록 신설 — console-api 고정경로 vs serve env-읽기, `/mnt/shared` 양쪽 지정 함정(silent 단절), `deploy.py` 보강 대안을 교차단계 의존으로 표기. (3) 성공기준 7에 경로 비대칭 검증 요구 반영. `C.ROOT`=`config.py:12` `parents[2]`, 컨테이너 WORKDIR 종속분은 `[검증 필요]` 유지.

---

## major

### MJ1. StatusBar "전파상태 배지"가 페이지 로드 시 데이터 출처가 없음
- 문제: 핸드오프 line 37은 StatusBar에 "active 버전 + **전파상태 배지**"를 그린다고 하나, `propagation`은 **approve/rollback 응답에서만** 나온다(`service.py:100,112`). 읽기 엔드포인트 `list_versions`는 `active`만 줄 뿐 `propagation`/serve-sync 상태를 반환하지 않는다(`service.py:218`). line 52의 재폴링도 "`GET /console/versions`로 **active** 재확인"일 뿐 전파 상태가 아니다.
- 귀결: 새 탭/새로고침 후(React state 소실) StatusBar는 전파 배지를 채울 출처가 없다. 프론트는 same-origin `/console/*`만 호출하므로(line 31·110) serve `/health`를 직접 못 본다(CORS·분리 원칙).
- 제안: 전파 배지는 **쓰기 직후 transient**임을 명시하거나, 로드 시 전파 상태를 줄 읽기 경로(예: console-api에 `/console/serve-sync?fs=` 같은 `/health` 프록시)를 명세하라. 현 상태로는 명명된 UI 요소의 데이터 계약이 비어 있음.

> **[reviser 응답]** 해소: 전파 배지를 **쓰기 직후 transient**로 명시 — StatusBar 컴포넌트 설명과 프론트 고유 계약 항목 2를 정정. "`propagation`은 approve/rollback 응답에서만 옴(`service.py:100,112`), `list_versions`는 active만 줌(`service.py:218`) → 새로고침/새 탭 후엔 배지를 비우거나 '상태 미상'으로 표기, active만 재확인"으로 데이터 계약 명확화. 로드 시 전파 상태가 필요하면 `/console/serve-sync?fs=` 프록시를 백엔드에 추가해야 하나 핸드오프 A 변경이라 범위 외 권고 `[검증 필요]`로 표기.

---

## minor

### mn1. incomplete 버전 approve 사전차단 미명세 (422 백스톱에만 의존)
- `gate_passed`는 `bucket=="incomplete"`면 `None`(`service.py:214`). M3 사전차단은 `gate_passed === false`만 본다(line 51) → `null`은 통과해 버튼 활성. 백엔드는 `_require_ready`로 `FileNotFoundError`→422(`service.py:78-79,86`)로 막고, 프론트 `post()`가 "422=미완성 메시지"를 표면화(line 66)하므로 계약은 닫힌다. 다만 "헛클릭 방지" 주장과 정합하려면 `gate_passed !== true`일 때(또는 `!ready`) 사전 비활성 권고.

> **[reviser 응답]** 해소: 프론트 고유 계약 항목 1과 성공기준 2를 `gate_passed !== true`(false 또는 incomplete의 null) 또는 `!ready`일 때 버튼 비활성으로 정정. `service.py:214`(null 출처)·`78-79,86`(422 백스톱) 근거 명시.

### mn2. AuditTrail "이 버전 관련" 필터 불가 — fs 필터만 존재
- line 45는 "이 버전 관련 감사(`/console/audit?fs=` 필터)"라 하나, audit 쿼리 파라미터는 `event_type/gate_passed/since/fs`뿐(`api.py:46`)이라 **버전 단위 필터가 없다**. fs 필터는 featureset 전체 이벤트를 준다. "이 버전 관련"은 클라이언트가 `from_version`/`to_version == 디렉토리명`으로 직접 거르는 것임을 명시하라. (감사의 `to/from_version`은 dir name이고 행은 stripped라 — B1 정정과 함께 매칭 키 재부착 필요.)

> **[reviser 응답]** 해소: AuditTrail 컴포넌트 설명을 "서버는 fs 필터만"으로 정정하고, 프론트 고유 계약 항목 6 신설 — "이 버전 관련"은 클라가 `from_version===dirName || to_version===dirName`로 거르며, 비교 키는 B1의 `toDirName(fs,v)` 디렉토리명과 정합(stripped 직접 비교 금지)임을 명시. `api.py:46` 근거.

### mn3. approve의 403 분기는 현 백엔드에서 사실상 dead path
- 핸드오프 line 21·66은 "403=PermissionError(미승인)" 분기를 둔다. 그러나 `service.approve`는 `deploy.swap(..., approved=True)`로 **항상 True를 보낸다**(`service.py:93`) → `PermissionError`("approved is not True", `deploy.py:84-85`)가 콘솔 경로에선 발생할 수 없다. rollback도 swap 미경유. 403 처리 자체는 무해하나 "미승인 차단"이 실제 작동 방어선이라는 인상은 정직하지 않음 — 주석으로 dead path임을 표기 권고.

> **[reviser 응답]** 해소: 에러 코드 bullet(코드현황)과 API 클라이언트 스니펫 주석에 "403=PermissionError는 콘솔 경로에서 dead path — approve는 `approved=True` 고정(`service.py:93`), rollback은 swap 미경유 → `PermissionError`(`deploy.py:84-85`) 발생 불가, 핸들러는 방어적 폴백일 뿐 작동 방어선 아님"을 명시.

### mn4. serve 단일 featureset(SERVE_FEATURESET) 토폴로지 미언급
- serve는 `SERVE_FEATURESET`(기본 vitals) 하나만 로드/리로드(`app.py:72,132,140`). console이 다중 fs를 다루면 `_propagate_and_confirm(fs)`가 단일 serve의 vitals만 리로드해 fs 불일치 가능. MVP(`CONSOLE_FEATURESETS=["vitals"]`, `config.py:10-12`)에선 정합하나, K8s 절(구현 3)에 "fs당 serve+console-api 1쌍" 또는 vitals MVP 한정임을 한 줄 명시 권고.

> **[reviser 응답]** 해소: K8s 토폴로지 절에 "단일 featureset 토폴로지(mn4)" bullet 신설 — serve는 `SERVE_FEATURESET` 하나만 로드(`app.py:72,132,140`), 본 핸드오프는 vitals MVP 한정(`config.py:10-12`)이며 다중 fs 확장 시 fs당 serve+console-api 1쌍 복제 필요(범위 외 `[검증 필요]`)임을 명시.

---

판정: **FAIL** — blocker 2건(B1 쓰기 version 식별자 역전, B2 ARTIFACTS_DIR 비대칭) / major 1건 / minor 4건. blocker≥1이므로 구현 진행 불가, 보완 후 재검토 필요.

---

## Round 1 — reviser 보완 요약

blocker 2건·major 1건·minor 4건을 모두 `handoff_frontend.md`에 반영(각 지적 밑 `[reviser 응답]` 참조). 핵심:
- **B1**: 읽기=stripped / 쓰기=디렉토리명(`gru_${fs}@<v>`) 비대칭을 코드현황·항목 5·성공기준 5·API 클라이언트(`toDirName`)에 반영.
- **B2**: console-api는 `ARTIFACTS_DIR` 미참조(`deploy.py:27`)·serve만 참조(`app.py:39`) 비대칭을 K8s env·공유저장소 의존·성공기준 7에 명시(고정경로 마운트 + silent 단절 함정).
- MJ1(전파 배지 transient)·mn1(`!== true` 사전차단)·mn2(audit 클라 필터)·mn3(403 dead path)·mn4(단일 fs 토폴로지) 반영.

재검토 대상: `handoff_frontend.md` v2. blocker 해소 여부를 다음 redteam 라운드가 대조.

---

## Round 2 — redteam

- 대상: `design/console/handoff_frontend.md` (명세부 v2, reviser 보완 `a575fa4`)
- 검토일: 2026-06-30
- 핵심 질문: (a) Round 1 blocker B1·B2가 표면이 아니라 근본에서 닫혔는가, (b) B1 디렉토리명 역전이 다른 경로(audit 필터·rollback)와 새 모순을 만들지 않는가
- 판정: **FAIL — blocker 1건 / major 0건 / minor 1건**

### B1 해소 확인 ✅
- 읽기=stripped / 쓰기=디렉토리명 비대칭이 코드와 정확히 정합. `_require_consistent`(`service.py:38-40`) 접두 강제 → `api.py:58` 422, `_strip_prefix`는 읽기 응답만(`service.py:211,218,237`), `api.py:20` 주석=dir name. `toDirName`(line 64-65) 이중접두 가드가 접두 포맷과 동일. audit `from_version`/`to_version`도 dir name으로 기록(`service.py:96`)되어 항목6(line 57) 비교 키와 일치. **B1 닫힘.**

### B2 해소 확인 ✅
- console-api `deploy.ARTIFACTS = C.ROOT/"deploy"/"artifacts"` 하드코딩·env 미참조(`deploy.py:27`, `service.py:25`), serve만 `ARTIFACTS_DIR` 참조(`app.py:39`). 핸드오프 line 126·138·140(`/mnt/shared` 함정)·고정경로 마운트 지침 정합. `C.ROOT`=`config.py:12 parents[2]`, WORKDIR 종속분 `[검증 필요]` 유지 적절. **B2 닫힘.**

### MJ1·mn1~mn4 정합 확인 ✅
- MJ1(전파 배지 transient, `service.py:100,112,218`)·mn1(incomplete `gate_passed=None` 사전차단, `service.py:214`/`77-79`)·mn2(audit 클라 필터, `api.py:46`)·mn3(403 dead path, `service.py:93,107`/`deploy.py:84-85`)·mn4(단일 `SERVE_FEATURESET`, `app.py:72,132,140`) 모두 코드 정합.

---

### blocker

#### BR2-1. 롤백 버튼이 archived(과거활성)로 제한되지 않음 — REGRESSED/미완성 challenger를 롤백으로 승격해 하드게이트(no_regression) 우회
- **문제**: 승인 버튼은 `gate_passed !== true || !ready`로 정밀 게이팅(line 52, 성공기준 2)하지만, **롤백 액션에 대한 대상 제한이 핸드오프 어디에도 없다**. 와이어프레임 Actions(line 44)는 모든 행에 승인/롤백 버튼을 둔다. 성공기준(line 149-156)에도 롤백 대상을 archived로 한정하는 조항이 없다.
- **근거 (백엔드는 롤백을 막지 않는다)**:
  - `service.rollback`(`service.py:103-112`)은 `_require_consistent`(fs 접두)만 검사하고 `_require_ready`도, validation 게이트도 호출하지 않는다. `deploy.rollback`(`deploy.py:93-95`)은 `set_alias`만 수행 — `approved`·`no_regression`·`.ready` 어떤 가드도 없음.
  - 하드게이트(no_regression)는 **오직 `swap`에만** 있다(`deploy.py:86-87`). 즉 승인 경로는 게이트가 걸리지만 롤백 경로는 게이트가 구조적으로 없다.
  - REGRESSED 버전도 `materialize`가 게이트 무관 자재화·`.ready` 부여(`deploy.py:42-43,59-67`) → `_classify`(`service.py:189-197`)에서 `challenger` 버킷·`gate_passed=false`(`service.py:214`)로 뜨고, 이 challenger 행에도 롤백 버튼이 달린다.
  - 흐름: REGRESSED challenger 행에서 롤백 클릭 → `toDirName`이 유효 dir name 생성 → `service.rollback` → `deploy.rollback` → `set_alias` → **REGRESSED 모델 활성화**. 결정 3·M3(`decisions.md:83·203`)의 "REGRESSED 비승격"이 롤백 경로로 뚫림. 미완성(`.ready` 없는) dir도 `_require_ready` 미적용이라 롤백 대상이 될 수 있어 손상 번들 활성화 가능.
- **B1 수정이 만든 새 노출인 이유**: v1에서는 롤백 요청이 stripped 값을 보내 `_require_consistent`가 422로 막아 모든 롤백이 실패(가려져 있었음). v2의 B1 수정이 모든 행에 well-formed 롤백 요청을 가능케 하면서 "롤백=무게이트 alias 교체" 구멍이 challenger/incomplete 행으로 노출됨. 설계 의도(`decisions.md:124` "롤백 대상은 과거 검증된 champion")는 명시돼 있으나, 그 전제를 강제하는 유일한 지점이 프론트인데 핸드오프가 그 제약을 빠뜨렸다.
- **제안**: 롤백 액션을 **`bucket === "archived"`(과거활성) 버전에만** 노출(승인 게이팅과 대칭). 성공기준에 "롤백 버튼은 archived에만 활성 — challenger/incomplete/champion에는 비활성" 추가. (백엔드 방어심화로 `deploy.rollback`에 past-active/ready 가드 추가는 `decisions.md` 5-A 방어심화와 동일선상의 H4r 교차단계 의존 — 본 단계 범위 밖이나 함께 표기 권고.)

> **[reviser 응답]** 해소: 롤백 버튼을 `bucket === "archived"`(과거활성=`_classify`의 `past_active` 매치, `service.py:189-197`) 행에만 활성하도록 4곳에 박음 — (1) 와이어프레임 Actions("승인(challenger 한정)/롤백(archived 한정)", `handoff_frontend.md:44`), (2) 프론트 고유 계약 **항목 7 신설**(백엔드 무게이트 사실 `service.py:103-112`·`deploy.py:93-95` vs 하드게이트는 `swap`에만 `deploy.py:86-87`, REGRESSED challenger 활성화 경로, "프론트가 1차 방어선" 명시), (3) API 클라이언트 `rollback` 주석(UI 게이팅 의무 명시), (4) 성공기준 **2b 신설**("롤백 버튼은 archived에만, challenger/incomplete/champion 비활성"). 백엔드 `deploy.rollback` past-active/`.ready` 가드는 H4r 교차단계 의존으로 `[검증 필요]`·범위 밖 표기, 프론트 게이팅이 본 단계 1차 방어선임 명시. 코드 직접 확인: `rollback`은 `_require_consistent`만(`service.py:104`), `_require_ready`·validation 호출 없음; `deploy.rollback`=`set_alias`만(`deploy.py:95`); `swap`에만 no_regression 게이트(`deploy.py:86-87`).

### minor

#### mr2-1. StatusBar active=null(심링크 소실) 표시 미명세
- `list_versions.active`는 alias 없으면 `null`(`service.py:44-45,218`). `_alert_missing_alias` 경로(`service.py:130-131`)에서 active가 null이 될 수 있다. StatusBar(line 38)는 null 처리를 명세하지 않아 빈 상태바가 정상/오류 어느 쪽인지 모호. "활성 alias 없음(심링크 소실)" 명시 표기 권고. (계약 위반 아님 — 표시 견고성.)

> **[reviser 응답]** 해소: StatusBar 컴포넌트 설명(`handoff_frontend.md:38`)과 성공기준 1에 "`active=null`(심링크 소실, `service.py:44-45,218`)이면 빈 상태바 대신 '활성 alias 없음(심링크 소실)' 명시 표기"를 추가 — 정상 빈 상태와 오류 상태를 구분.

---

판정: **FAIL** — blocker 1건(BR2-1 롤백 대상 게이팅 부재 → 하드게이트 우회) / major 0건 / minor 1건. **B1·B2는 근본에서 닫혔음을 확인**했으나, B1 수정이 노출한 롤백 경로의 하드게이트 우회가 새 blocker. blocker≥1이므로 보완 후 재검토.

---

## Round 2 — reviser 응답

blocker 1건(BR2-1)·minor 1건(mr2-1)을 `handoff_frontend.md`에 반영(각 지적 밑 `[reviser 응답]` 참조). 핵심:
- **BR2-1(롤백 무게이트 우회)**: 롤백 버튼을 `bucket === "archived"`(과거활성) 행에만 활성하도록 와이어프레임 Actions·프론트 고유 계약 항목 7(신설)·API 클라이언트 `rollback` 주석·성공기준 2b(신설) 4곳에 박음. 백엔드 롤백 경로 무게이트(`service.py:103-112`→`deploy.rollback` set_alias만 `deploy.py:93-95`; 하드게이트는 `swap`에만 `deploy.py:86-87`) 사실을 코드 근거로 명시하고, "프론트 게이팅이 본 단계 1차(유일) 방어선"·백엔드 `deploy.rollback` 가드는 H4r 교차단계 의존 `[검증 필요]`·범위 밖으로 표기.
- **mr2-1(active=null 표기)**: StatusBar 설명·성공기준 1에 "활성 alias 없음(심링크 소실)" 명시 표기(`service.py:44-45,218`) 추가 — 빈 상태바 정상/오류 모호 해소.

재검토 대상: `handoff_frontend.md` v3. BR2-1 게이팅이 archived 버킷에 정확히 걸렸는지(REGRESSED challenger·incomplete·champion 비활성) 다음 redteam 라운드가 대조.

---

## Round 3 — redteam

- 대상: `design/console/handoff_frontend.md` (명세부 v3, reviser 보완 `1bb58bb`)
- 검토일: 2026-06-30
- 핵심 질문: BR2-1(롤백 무게이트 우회) 보완이 표면이 아니라 **근본에서** 닫혔는가 — archived 게이팅이 `_classify` 실제 정의와 정확히 맞고, REGRESSED challenger·incomplete·champion을 모두 차단하며, 정당한 롤백(과거 champion=archived)은 막지 않는가. 새 모순은 없는가.
- 판정: **PASS — blocker 0 / major 0 / minor 1**

### BR2-1 해소 확인 ✅
롤백 게이팅이 코드와 정확히 정합하며 4곳에 일관되게 박혔다.
- **4곳 일관성**: (1) 와이어프레임 Actions `handoff:44` "롤백(archived 한정)", (2) 프론트 고유 계약 항목 7 `handoff:58` "`bucket === "archived"`에만 노출/활성, champion·challenger·incomplete disabled", (3) API 클라이언트 주석 `handoff:74-76`, (4) 성공기준 2b `handoff:155`. 네 곳 동일 조건(`bucket === "archived"`) — 표면-내부 불일치 없음.
- **`_classify` 실제 정의와 정합**(`service.py:189-197`, 우선순위 champion(active) > archived(past_active) > challenger(ready) > incomplete):
  - REGRESSED challenger(과거활성 아님, `.ready` 보유): `challenger` 버킷 → **롤백 미노출**. 승인도 항목1 `gate_passed !== true`로 비활성(`service.py:214`). 양 경로 모두 차단. ✓
  - incomplete(`.ready` 없음): `incomplete` 버킷 → 롤백 미노출. ✓
  - champion(현재활성): `champion` 버킷 → 롤백 미노출(자기 자신 롤백 무의미). ✓
  - archived(과거 활성, 현재 비활성): `past_active` 매치 → **롤백 노출** = `decisions.md:124` 정당 워크플로, 막히지 않음. ✓
- **백엔드 무게이트 정직성 명시**: `handoff:58·74-76·155`이 "백엔드 롤백 경로 무게이트(`service.py:103-112`→`deploy.rollback` set_alias만 `deploy.py:93-95`; 하드게이트는 `swap`에만 `deploy.py:86-87`) → 프론트 게이팅이 본 단계 1차(유일) 방어선" 명시. 코드 1:1 일치 확인.
- **백엔드 가드 범위 밖 표기**: `deploy.rollback` past-active/`.ready` 가드를 `decisions.md` 5-A 방어심화 선상 H4r 교차단계 의존 `[검증 필요]`로 표기 — 프론트 핸드오프에서 백엔드 변경 요구 안 함, 옳은 처리. **BR2-1 해소 확인.**

### mr2-1 해소 확인 ✅
`active=null`(심링크 소실) 표기가 StatusBar `handoff:38`·성공기준 1 `handoff:153`에 "활성 alias 없음(심링크 소실)"로 반영. 출처 `service.py:44-45,218` 정합. **mr2-1 해소 확인.**

### 새 모순 점검 ✅
- 정당 롤백 차단 없음: archived 비면 롤백 후보 0(의도된 "거짓 복원 안 함"), 과거 champion은 archived로 분류돼 롤백 가능.
- REGRESSED가 archived로 새지 않음: archived 진입=`_past_active_ids`의 `to_version`, APPROVE는 `swap` 게이트(no_regression) 통과분만 → 정상 콘솔 경로 archived=과거 검증 champion.
- 승인 경로 회귀 없음: 항목1 게이팅 유지, REGRESSED challenger는 승인·롤백 양 경로 펜싱.

### minor
- **[minor] 와이어프레임 Actions "승인(challenger 한정)" 서술이 항목1 정밀조건보다 느슨**(`handoff:44`) — 실제 승인 활성은 항목1 `gate_passed !== true || !ready`면 비활성(challenger 중 gate_passed===true·ready만). 항목1이 정밀 조건을 명시해 계약은 닫힘 → 서술 정밀도 minor, 차단 사유 아님.

### 기 인지된 한계 (정직성 확인)
- UI 우회(직접 `POST /console/rollback`로 REGRESSED dir) 시 백엔드 무게이트로 활성화 가능 — 그러나 `handoff:58`이 "프론트가 1차(유일) 방어선" 정직히 명시, 백엔드 가드는 `decisions.md` 5-A(H4r 교차단계 `[검증 필요]`)로 본 단계 범위 밖 확정. 프론트 핸드오프 기준 blocker 아님. 정직 표기됨.

---

판정: **PASS** — blocker 0 / major 0 / minor 1. BR2-1·mr2-1 근본 해소 확인. archived 게이팅이 `_classify`(`service.py:189-197`) 실제 정의와 정확 정합, REGRESSED challenger·incomplete·champion 전부 차단, 정당 롤백(과거 champion=archived) 유지. 백엔드 무게이트·프론트 1차 방어선·H4r 교차단계 `[검증 필요]` 정직 명시. 새 모순 없음. **blocker 0 → 구현 진행 가능.**

---

## 루프 종료 요약

- **3라운드 만에 통과** (blocker 0).
- Round 1: blocker 2 (B1 쓰기 version 식별자 역전 · B2 ARTIFACTS_DIR 비대칭) → `a575fa4`
- Round 2: blocker 1 (BR2-1 롤백 무게이트 우회 — B1 수정이 노출) → `1bb58bb`
- Round 3: PASS (blocker 0 / minor 1 잔존: 와이어프레임 승인 서술 정밀도)
- 잔여 minor: 와이어프레임 "승인(challenger 한정)" 서술이 항목1 정밀조건보다 느슨(계약은 항목1이 닫음).
- 기 인지 한계(범위 밖, 정직 표기됨): 백엔드 롤백 경로 무게이트 — UI가 1차 방어선, 백엔드 가드 추가는 H4r 교차단계 `[검증 필요]`.
