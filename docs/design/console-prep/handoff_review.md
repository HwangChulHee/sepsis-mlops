# Console-Prep 핸드오프(명세부) 레드팀 검토 — 라운드 로그

> 대상: `docs/design/console-prep/handoff.md` (명세부). 설계부 = `decisions.md`(결정 1~7, PASS).
> 규약: redteam 지적 원문 보존 + 각 항목 아래 `[reviser 응답]`으로 해소 대조.

---

## 라운드 1 (redteam 원문)

- 대상: docs/design/console-prep/handoff.md (신규, 첫 검토, 명세부)
- 대상 commit: cb39acc (working tree clean)
- 검토일: 2026-06-29
- 핵심 질문: 핸드오프만으로 구현자가 막힘없이 짤 수 있는가 + decisions.md 결정 1~7을 코드 정합·끊김 없이 구현 명세로 옮겼는가
- 판정: HOLD — blocker 1건 (major 5 / minor 5)

코드 대조: handoff.md "코드 현황"(line 9~15)의 출처표기는 실제 코드와 일치한다(pipeline.py:31-47/50/94-98, validate.py:31-43, deploy.py:28-43/46-48/55-70, app.py:34-44, bundle.py:102). 문제는 표기가 아니라, 서빙 reload 명세가 이미 존재하는 드리프트 서브시스템과 정합하지 않아 결정 6의 핵심 흐름(/drift)이 끊기는 데 있다.

### PASS
- 구현 1·2 메타 주입 — 코드 정합 (handoff:21-48). RetrainResult에 run_id/git_commit/seed 3종 추가 + meta.json에 run_id 기록은 코드 현실과 맞다. Bundle은 run_id 필드를 가지며(bundle.py:44) /health가 s["bundle"].run_id를 반환(app.py:80), load_bundle_from_dir가 meta.get("run_id", str(d.name)) 폴백(bundle.py:102)이므로 meta.json.run_id가 채워지면 alias명 오염이 닫힌다 → MJ2 데이터 측면 해소. (e)의 "타겟 dir의 meta.json.run_id와 비교"도 결정 4와 일치.
- 구현 3 JSON 필드 출처 정합 (handoff:59-63). dataclasses.asdict(validation)은 ValidationResult의 10개 필드 전부(no_regression·4개 헤드라인 수치·cross_site_claim·distribution·note, validate.py:31-43)를 포함 → 결정 1 요구 충족. retrain.json의 epochs/val_loss/n_train_pids/n_b_retrain/n_b_holdout는 RetrainResult에 이미 존재(pipeline.py:39-46), b_split_seed=rr.seed·run_id·git_commit은 구현 1이 주입 → MJ1 영속 지점 도달 닫힘.
- os.replace 원자 기록 + .ready 마커 — 결정 7 기법 명세됨 (handoff:65-77). 파일별 os.replace는 동시 리더의 torn-read를 배제하고, .ready를 마지막에 쓰는 AND 완성 표식은 결정 7 line 102가 "완성 마커 파일"로 명시 허용한 기법이다 → 설계부가 위임한 기법이 실제로 명세됨. materialize가 두 JSON을 연속 기록하므로 "하나만 보이는" 창은 리더가 .ready 부재로 미완성 처리.
- 누수 불변 보존 (handoff:135, 성공기준 7). 변경은 영속·로딩·메타데이터 계층 한정이고 retrain()의 split/stats/fill 로직(pipeline.py:61-77)을 건드리지 않는다.

### blocker
#### B1 — 서빙 reload(구현 4b)가 기존 드리프트 서브시스템과 비정합 → /drift 흐름 단절 + 결정 6의 스큐 방지가 실제로 미달성
- 위치: handoff.md 구현 4(b), line 101-107 (특히 _DS.update(reference=load_reference(version_dir))).
- 문제: 핸드오프는 state()/_load_all만 새로 쓰고 drift_state()(app.py:97-110)·drift_endpoint()(app.py:113-122)를 일절 언급·수정하지 않는다. 끝까지 흐름추적하면 세 군데서 끊긴다:
  1. _DS 스키마 불일치: _load_all은 _DS에 키 reference만 넣는다. 그러나 살아있는 drift_endpoint는 ds["ref"]·ds["thr"]·ds["min_patients"]를 읽고(app.py:118,120-121), 이는 drift_state()가 채운다. drift_state()는 if "ref" not in _DS(app.py:100)로 분기하는데 _load_all이 넣은 키는 reference라 "ref"는 여전히 부재 → drift_state()가 옛 경로를 그대로 재실행한다. _load_all의 _DS 작업은 아무도 소비하지 않는 죽은 코드가 된다.
  2. load_reference 인자 타입 오류: R.load_reference(path)는 np.load(path)로 파일(.npz) 경로를 받는다(reference.py:83-84). 핸드오프는 load_reference(version_dir)로 디렉토리를 넘긴다 → 첫 요청에서 _load_all이 즉시 예외. (또한 app.py는 R.load_reference로만 임포트돼 있어 bare load_reference는 미정의.)
  3. 결정 6의 스큐 방지가 실제로 미달성: drift_state()가 살아있으므로 드리프트 baseline은 SERVE_BUNDLE_DIR에서 온다(app.py:101-104). 그런데 결정 5/mn3가 SERVE_BUNDLE_DIR를 alias 통일로 superseded시키면 → 미설정 시 R.build_reference(SERVE_FEATURESET)로 A-train을 새로 빌드(app.py:106)한다. 활성 alias 번들의 reference.npz(재학습 후 = A-train+B-retrain)와 다른 baseline → 거짓 드리프트. 설정된 채로 두면 alias swap을 무시하는 stale reference. 어느 쪽이든 결정 6이 막으려던 모델↔reference 스큐가 그대로 남는다.
- 근거: app.py:97-122 (drift_state/drift_endpoint 미수정), reference.py:83-84 (load_reference는 파일 경로), handoff:106. decisions.md 결정 6(line 85-90)은 "_S·_DS를 동일 버전으로 동시 로드, 첫 요청 lazy-load도 같은 해석 시점"을 요구하나, 핸드오프의 _DS 경로는 drift_state의 lazy 경로와 화해되지 않았다. "드리프트 로직"은 범위 외지만 결정 6이 다루는 reference 소스 일치는 명백히 범위 내다(핸드오프 스스로 _DS를 건드려 범위에 끌어들였다).
- 제안: drift_state()/drift_endpoint()를 reload 경로와 일관되게 재명세하라 — (a) _load_all이 _DS에 ref(=R.load_reference(version_dir/"reference.npz"))·thr(=synthetic.calibrate(...))·min_patients를 드리프트가 읽는 스키마 그대로 채우거나, (b) drift_state()가 alias 타겟의 reference.npz를 읽도록 고치고 reload 시 재호출되게 하라. reload 시 thr 재캘리브레이션(n_trials=300, 비용 큼) 정책도 명세하라. load_reference에 디렉토리가 아닌 reference.npz 파일 경로를 넘기도록 교정.

> **[reviser 응답]** 해소: 구현 4를 전면 재명세했다(handoff.md:80-167). (1) `_load_all`이 `_DS`를 **drift가 읽는 스키마 그대로**(`ref`·`thr`·`min_patients`) 채운다 — 옛 `reference` 죽은 키 제거(4b). (2) `R.load_reference(version_dir/"reference.npz")` 파일 경로로 교정, 임포트는 `R.load_reference`(이미 임포트됨)·`load_bundle_from_dir`(추가) 명시(4 임포트절·4b). (3) `drift_state()`를 `state()`와 동일하게 `_load_all(_resolve_alias(...))`로 라우팅해 **SERVE_BUNDLE_DIR/build_reference 옛 경로를 superseded**(4d) — drift baseline = 활성 alias 번들 `reference.npz`(= 모델과 동일 version_dir), 스큐 닫힘. `drift_endpoint()`는 `ds["ref"]·ds["thr"]·ds["min_patients"]` 계약을 그대로 만족해 수정 불필요(4d 명시). thr 재캘리브레이션 정책(모든 `_load_all`에서 새 reference로 재캘리브, n_trials 기본 300·`DRIFT_CAL_TRIALS` 조정, lock 안·재바인딩 전 → 리더 비블로킹)을 4c에 못 박음.

### major
#### MJ-a — MLflow run의 tracking_uri·experiment 미명세 → run_id가 콘솔 deep-link에서 해석 불가할 위험
- 위치: handoff:30-37 (with mlflow.start_run() as run:).
- 문제: 결정 3의 목적은 "콘솔이 이 run_id로 MLflow deep-link 생성"이다(decisions.md:52). 그러나 핸드오프는 start_run()에 tracking_uri·experiment를 지정하지 않는다. 미지정 시 run은 기본 ./mlruns 파일 스토어에 기록되는데, 기존 번들 로딩은 sqlite:///{C.ROOT}/mlflow.db·experiment "h2"를 쓴다(bundle.py:115,118). 두 스토어가 다르면 콘솔이 이 run_id를 조회해도 찾지 못한다 = 댕글링 식별자.
- 제안: 재학습 run의 tracking_uri(예: bundle.py와 동일한 sqlite)·experiment명을 명세하라.

> **[reviser 응답]** 해소: 구현 1에 `mlflow.set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")`(= bundle.py:115 단일 스토어)·`mlflow.set_experiment("retrain")`을 명시(handoff.md:30-44). tracking_uri는 콘솔 deep-link 해석의 필수 조건이라 bundle.py와 단일 진실원천 공유. experiment명 "retrain"은 h2 학습 run과 분리하는 명세 수준 선택([우리 결정]); run_id는 스토어 내 전역 유일이라 deep-link by run_id는 experiment 무관 해석.

#### MJ-b — materialize 시그니처를 validation 필수로 바꾸면서 기존 호출부 미식별
- 위치: handoff:54 (def materialize(retrain_result, version, *, validation, ...)).
- 문제: validation이 keyword-only 필수 인자가 된다. 기존 호출부 scripts/h4/h4r_c_smoke.py:51(deploy.materialize(rr, "v1-retrain", root=ROOT))은 validation 없이 호출 → 깨진다. 또 게이트 실패(no_regression=False) 버전도 콘솔이 표시해야 하므로 materialize는 검증 통과/실패 무관하게 호출돼야 하는데, 그 불변(항상 materialize)도 명시 안 됨.
- 제안: 기존 materialize 호출부(h4r_c_smoke.py:51) 갱신을 명세에 포함. "검증 결과와 무관하게 materialize 호출(REGRESSED도 challenger로 표시)"을 한 줄 못 박아라.

> **[reviser 응답]** 해소: 구현 3에 (1) 기존 호출부 갱신을 명세 — `scripts/h4/h4r_c_smoke.py:51`을 `deploy.materialize(rr, "v1-retrain", validation=vr, root=ROOT)`로(handoff.md 구현 3 "호출부 갱신"절). (2) "**materialize는 게이트 통과/실패 무관하게 항상 호출**(REGRESSED도 challenger로 영속·표시); 게이트는 `swap`에서만 강제"라는 불변을 한 줄 못 박음. 대상 파일에 `scripts/h4/h4r_c_smoke.py` 추가.

#### MJ-c — 영속 eps를 하드코딩 → 실제 게이트 epsilon과 silent desync 가능
- 위치: handoff:59 ("eps": 0.02), 78.
- 문제: eps는 validate(*, eps=0.02)의 함수 인자일 뿐 ValidationResult 필드가 아니다(validate.py:31-43,46). materialize는 validation 객체만 받으므로 실제 사용된 eps를 알 수 없고, 핸드오프는 상수 0.02를 박는다. validate가 다른 eps로 호출되면 영속된 eps는 거짓이 된다 — 감사용 게이트 임계값인데 silent하게 틀릴 수 있다.
- 제안: 실제 eps를 materialize로 전달하거나 ValidationResult에 eps 필드를 추가해 실측값을 영속하라.

> **[reviser 응답]** 해소: 제안 (2)로 — `ValidationResult`에 `eps: float = 0.02` 필드 추가, `validate()`가 실제 사용한 eps를 `eps=eps`로 반환(handoff.md "구현 3-pre: validate.py" 절). 그러면 `dataclasses.asdict(validation)`이 eps를 자동 포함 → materialize의 하드코딩 `"eps": 0.02` 제거. 영속된 eps == 게이트가 쓴 eps(단일 출처, desync 불가). 이는 결정 1("validation.json에 eps 영속")의 충실한 구현이지 새 결정이 아님. 대상 파일에 `validate.py` 추가.

#### MJ-d — in-place reload 락이 writer만 직렬화 → 리더 가시성은 비원자
- 위치: handoff:99-107 (_LOCK, _S.clear(); _S.update(...)).
- 문제: _LOCK은 _load_all(writer)만 보호한다. /predict·/health·/drift는 락 없이 _S/_DS를 읽는다. _S.clear()와 _S.update()는 2단계라, 그 사이 리더가 _S["pred"]에 접근하면 transient KeyError/혼합 상태를 본다(FastAPI sync 엔드포인트는 threadpool 동시 실행). 결정 6이 격상한 "reload 원자성"이 리더 관점에서 미충족.
- 제안: 새 dict를 완성 후 단일 재바인딩(atomic rebind)하거나 리더도 _LOCK(혹은 RW)을 잡게 하라.

> **[reviser 응답]** 해소: 제안의 atomic rebind 채택 — `_load_all`이 lock 안에서 `new_s`·`new_ds`를 **완성한 뒤 모듈 전역 `_S`·`_DS`를 1회 재바인딩**(`global _S, _DS; _S = new_s; _DS = new_ds`), `clear()/update()` 2단계 제거(handoff.md 구현 4b). CPython 이름 재바인딩은 GIL 하 단일 STORE이라 리더는 옛 dict 또는 새 dict만(부분상태 없음). `state()`/`drift_state()`가 반환한 dict를 잡은 리더는 요청 동안 일관 스냅샷 → 리더 락 불필요. 두 재바인딩 사이 1 바이트코드 창은 각 엔드포인트가 둘 중 하나만 읽고 새 _DS도 같은 version_dir 산이라 모델-reference 짝은 항상 동일 버전(4b 주석 명시).

#### MJ-e — 서빙 ARTIFACTS 기본값이 cwd 의존 상대경로 → deploy.py와 불일치
- 위치: handoff:84 (ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", "deploy/artifacts"))).
- 문제: deploy.py는 ARTIFACTS = C.ROOT / "deploy" / "artifacts"(절대, deploy.py:25)로 alias gru_<fs>를 기록한다. 서빙이 상대 "deploy/artifacts"를 기본값으로 쓰면 cwd에 따라 deploy가 쓴 alias와 다른 경로를 해석할 수 있다(컨테이너에서 ARTIFACTS_DIR 미설정 시).
- 제안: 서빙도 C.ROOT 기준 절대경로를 기본값으로 두어 deploy.py와 단일 진실원천을 공유하라.

> **[reviser 응답]** 해소: 서빙 기본값을 `Path(os.environ.get("ARTIFACTS_DIR", str(C.ROOT / "deploy" / "artifacts")))`로 교정(handoff.md 구현 4a). deploy.py:25(`C.ROOT/"deploy"/"artifacts"`, 절대)와 단일 진실원천 공유 — cwd 의존 제거. `C`는 app.py에 이미 임포트됨(app.py:22).

### minor
> **[reviser 응답 — mn1]** 해소: 구현 1에 `_git_commit()` 헬퍼 명세 — `git rev-parse HEAD`로 SHA, `git status --porcelain` 비어있지 않으면 `+dirty` 접미사, `CalledProcessError`/`FileNotFoundError`(git 부재·non-repo)면 `"unknown"` 폴백(handoff.md 구현 1).
>
> **[reviser 응답 — mn2]** 해소: 구현 3에 ".ready 검사 정렬" 한 줄 추가 — 콘솔 명세부가 완성 표식을 `.ready` 존재로 검사하도록 정렬해야 함을 명시(콘솔 design의 "두 파일 AND"는 `.ready`로 구현됨, 둘 다 fail-closed). 범위 외(콘솔)지만 교차 계약으로 못 박음.
>
> **[reviser 응답 — mn3]** 해소: B1과 함께 교정 — 구현 4 임포트절에 `load_bundle_from_dir`·`threading` 추가, `R.load_reference`·`synthetic`·`C`·`Path`는 기존 임포트 재사용 명시(handoff.md 구현 4 임포트절).
>
> **[reviser 응답 — mn4]** 해소: 코드 현황의 RetrainResult "있음" 목록에 `aval_raw·bholdout_data·aval_data` 추가(handoff.md:9, pipeline.py:42-44).
>
> **[reviser 응답 — mn5]** 해소: `validated_at`를 UTC로 — `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())`(Z 접미사로 UTC 명시, handoff.md 구현 3).

---

## 라운드 2 (redteam) — B1 보완 재흐름추적

- 대상: `docs/design/console-prep/handoff.md` (명세부, reviser 커밋 `e63ec08` 반영본)
- 검토일: 2026-06-29
- 핵심 질문: B1 보완이 reload→drift 사슬을 실제로 닫았는가(구멍을 옮기지 않았는가) + MJ-c eps 필드 추가가 설계부 결정 1~7 범위 내 구현인가 + MJ-a/b/d/e·minor가 코드·기존 결정과 모순 없이 끼워졌는가
- **판정: PASS (blocker 0)** — major 0 / minor 2

### 흐름추적 결과 (B1 reload→drift, end-to-end)
핸드오프가 부르는 심볼을 전부 1차 코드와 대조했고, 사슬은 닫혔다.
- **심볼 실재**: `R.load_reference(version_dir/"reference.npz")`는 `reference.py:83` `np.load(path)`로 .npz 파일 경로를 받음(B1-2 교정 정확). `synthetic.calibrate(ref, window_n=, n_trials=)`는 `synthetic.py:56` 시그니처와 일치. `load_bundle_from_dir`는 `bundle.py:95` 실재. 임포트는 mn3가 `load_bundle_from_dir`·`threading`만 추가 — 정확.
- **(a) thr 재캘리브 비폭주**: `drift_state()`는 `if "ref" not in _DS`일 때만 `_load_all` 호출. `state()`가 먼저 `_load_all`을 돌리면 `_DS["ref"]`가 차 있어 재캘리브 안 함 → 캘리브는 **프로세스 부팅당 1회 + /admin/reload당 1회**. n_trials 300 비용은 lock 안·재바인딩 전이라 리더 비블로킹(4c). 정합·성능 문제 없음.
- **(b) 원자 재바인딩 일관**: `_load_all`이 lock 안에서 `new_s`·`new_ds` 완성 후 `global _S,_DS; _S=new_s; _DS=new_ds`로 1회씩 재바인딩. 두 재바인딩 사이 창에 `_S=new`/`_DS=old`가 잠깐 갈리지만 **어느 단일 엔드포인트도 _S·_DS를 동시에 읽지 않음**(/predict=_S만 app.py:64-73, /drift=_DS만 app.py:117-122) → 인트라-리퀘스트 스큐 불가. clear/update가 아닌 재바인딩이라 리더가 잡은 옛 dict는 절대 변이 안 됨(완전 스냅샷). MJ-d 논증 건전.
- **스큐 종결**: reload 시 `_S`·`_DS` 모두 `_resolve_alias(fs)`가 해석한 동일 version_dir에서 파생 → 결정 6의 모델↔reference 동일버전 충족.

### PASS (근거)
- **B1 reload↔drift 정합** — `_DS` 스키마(ref·thr·min_patients)가 살아있는 `drift_endpoint` 읽기 계약(app.py:118·120·121)과 정확히 일치, `drift_state()`가 `state()`와 동일 `_load_all` 경로로 라우팅돼 옛 `SERVE_BUNDLE_DIR`/`build_reference` 죽은 경로 superseded. 라운드1 B1의 세 단절(스키마·인자타입·baseline 소스) 모두 닫힘.
- **MJ-c (eps 필드) 설계부 범위 내** — `decisions.md:28`이 validation.json에 eps를 명시 요구, `:32`가 "eps는 ValidationResult 필드 아님→영속 시 별도 주입"을 이미 못 박음, `:102·125`가 직렬화 기법을 명세부 위임. 따라서 `ValidationResult.eps` 필드 추가는 **새 설계 결정이 아니라 결정 1의 충실한 구현**. default(=0.02) 맨 뒤 추가 → 데이터클래스 유효, 기존 호출부(SimpleNamespace) 무영향. asdict가 eps 자동 포함→영속 eps==게이트 eps.
- **MJ-a** — `set_tracking_uri(sqlite:///{C.ROOT}/mlflow.db)`가 bundle.py:115 동일 스토어와 일치→run_id 조회 가능. retrain() 내 load_bundle의 "h2" experiment 읽기와 충돌 없음.
- **MJ-b** — 유일 호출부 `h4r_c_smoke.py:51` 갱신 명세, vr은 직전 validate 결과로 실재. "게이트 무관 항상 materialize, 강제는 swap에서만" 불변 명시.
- **MJ-e** — 기본값 `C.ROOT/deploy/artifacts`가 deploy.py:25와 동일. 컨테이너 ROOT=/app라 alias 정확 해석.
- **누수 불변** — split/stats/fill·환자단위 B분할·mask OFF 불변(pipeline.py:22-28,61-86).

### minor (다음 단계 막지 않음)
- **mn-A.** 부팅 시 동시 첫 요청(/predict·/drift)이 빈 상태로 도착하면 lock 밖 lazy 체크 때문에 두 스레드가 각각 `_load_all`→n_trials=300 캘리브 2회(순차). 정확성 무해(둘 다 동일 version_dir, 마지막 재바인딩 승), 부팅 1회성 낭비뿐. lock 내 idempotent 더블체크 1줄 권고.
- **mn-B.** `deploy/Dockerfile:20`의 `SERVE_BUNDLE_DIR` env가 死 변수로 잔존(새 코드가 무시). C.ROOT=/app라 같은 경로 해석돼 무해. 배포 매니페스트 갱신 시 제거 권고(handoff 범위 외 "배포 매니페스트 [검증 필요]"에 흡수).

### 판정
**PASS — blocker 0.** 라운드1 B1은 reload→drift 사슬을 끝까지 닫았고 구멍을 옮기지 않았다. MJ-c는 결정 1이 이미 요구한 eps 영속의 구현이지 새 설계 결정이 아니다. MJ-a/b/d/e·minor 모두 코드·기존 결정과 모순 없이 끼워졌다. 남은 2건은 정확성 무해한 minor. **명세부 검토 통과 — 다음 단계(spec-writer TDD) 진입 가능.**
