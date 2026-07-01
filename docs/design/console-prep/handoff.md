# Console-Prep 구현 핸드오프 (명세부) — H4 백엔드 보강

> **전제**: `design/console-prep/decisions.md`(설계부) 2라운드 검토 통과(blocker 0). 본 문서는 그 결정 1~7의 *구현 방법*을 자립형으로 명세한다. 설계 근거는 decisions.md 참조.
> **워크플로우**: 검토(`handoff_review.md`) 통과 → spec-writer TDD → 구현.
> **대상 파일**: `src/sepsis/retrain/{pipeline.py, deploy.py, validate.py}`, `src/sepsis/serve/app.py`, `scripts/h4r_c_smoke.py`(호출부 갱신). (콘솔 API/UI·FS↔DB 일관성은 범위 외.)
> **상태**: 명세부 v2 — 레드팀 라운드 1 반영(B1 reload↔drift 정합 재명세, MJ-a~e, mn1~5).

## 코드 현황 (구현 시작점)

- `RetrainResult`(pipeline.py:31-47): `featureset·input_dim·hp·tau·stats·model·b_retrain·b_holdout·train_pids·aval_raw·bholdout_data·aval_data·epochs·val_loss·mask_on` **있음**(mn4: aval_raw/bholdout_data/aval_data 포함, pipeline.py:42-44). `run_id·git_commit·seed` **없음**.
- `retrain()`(pipeline.py:50): `seed` 인자로 받으나 `RetrainResult`에 전달 안 함. 끝에서 `RetrainResult(...)` 생성(pipeline.py:94-98).
- `ValidationResult`(validate.py:31-43): `bholdout_util·bholdout_prauc·new_aval_util·old_aval_util·new_aval_prauc·old_aval_prauc·no_regression·cross_site_claim·distribution·note`. `eps` **없음** → 구현 3-pre로 필드 추가(MJ-c). timestamp는 영속 시 주입.
- `materialize()`(deploy.py:28-43): version dir에 `model.pt·pre.npz·meta.json·reference.npz` 기록. `meta.json` = `featureset·hp·input_dim·tau·version·trained_on`.
- `state()`(serve/app.py:34-44): `SERVE_BUNDLE_DIR` 있으면 그 dir, 없으면 featureset MLflow. `_S`에 영구 캐시.
- `active_version()`(deploy.py:46-48): `os.readlink`로 alias 타겟 **디렉토리명** 반환.
- `swap()`(deploy.py:55-65): prev 버전명 반환. `rollback()`(deploy.py:68-70): prev명 받아 alias 되돌림.

---

## 구현 1: 재학습 메타 3종 주입 (결정 3) — `pipeline.py`

`RetrainResult`에 필드 3개 추가:
```python
run_id: str = ""
git_commit: str = ""
seed: int = 0
```

`retrain()`을 MLflow run으로 감싸고 3종 주입. **tracking_uri·experiment를 명시**(MJ-a) — 미지정 시 run이 기본 `./mlruns` 파일 스토어로 가 콘솔 deep-link 조회가 빗나간다:
```python
import mlflow, subprocess
from sepsis import config as C   # 이미 임포트됨

mlflow.set_tracking_uri(f"sqlite:///{C.ROOT}/mlflow.db")   # = bundle.py:115 단일 스토어(콘솔 deep-link 해석)
mlflow.set_experiment("retrain")                           # h2 학습 run과 분리; run_id는 스토어 내 전역 유일

# retrain() 본문을 with mlflow.start_run() as run: 로 감싼다
run_id = run.info.run_id
git_commit = _git_commit()
mlflow.log_params({"featureset": featureset, "seed": seed, **hp})
mlflow.log_metrics({"epochs": res.n_epochs, "val_loss": res.best_val_loss})
# RetrainResult(...) 생성 시 run_id=run_id, git_commit=git_commit, seed=seed 추가
```
- `tracking_uri`는 **콘솔 deep-link 해석의 필수 조건**이라 bundle.py와 단일 진실원천([우리 결정] — bundle.py:115 동일 sqlite). `experiment="retrain"`은 h2 학습 run 오염 방지용 명세 선택([우리 결정]); MLflow run_id는 스토어 내 전역 유일이라 deep-link는 experiment 무관 해석.

**git_commit 헬퍼 (mn1)** — `git rev-parse`는 dirty를 감지 못하고 non-repo/git부재 폴백도 필요:
```python
def _git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"                       # non-repo / git 부재
    dirty = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    return sha + ("+dirty" if dirty else "")   # 워킹트리 더티면 명시
```
- **git_commit 용도** = 감사·MLflow 링크용 식별자. **재현은 `seed` 몫**(git_commit은 재현 키 아님).

## 구현 2: `meta.json`에 run_id (결정 4) — `deploy.py` `materialize()`

`meta.json` dict에 `run_id` 추가:
```python
{"featureset": rr.featureset, "hp": rr.hp, "input_dim": rr.input_dim,
 "tau": rr.tau, "version": version, "trained_on": "A-train+B-retrain",
 "run_id": rr.run_id}   # ADDED — MLflow 연결 키의 단일 권위 출처
```
- `load_bundle_from_dir`의 `meta.get("run_id", str(d.name))` 폴백(bundle.py:102)이 이제 실제 run_id를 받아 `/health` 식별 정상화.

## 구현 3-pre: `ValidationResult`에 `eps` 필드 (결정 1, MJ-c) — `validate.py`

영속 eps를 하드코딩하면 `validate(*, eps=)`가 다른 값으로 불릴 때 감사용 게이트 임계값이 silent하게 거짓이 된다(MJ-c). **게이트가 실제 쓴 eps를 그대로 영속**하려면 `ValidationResult`에 필드를 추가한다:
```python
@dataclass
class ValidationResult:
    ...                # 기존 10필드
    eps: float = 0.02  # ADDED — 게이트에 실제 사용된 eps (감사용 단일 출처)
```
`validate()`의 반환에 `eps=eps`를 추가:
```python
    return ValidationResult(..., no_regression=bool(no_reg), eps=eps, note=...)
```
- 이후 `dataclasses.asdict(validation)`이 eps를 자동 포함 → materialize는 **하드코딩 없이** 실측 eps를 영속(영속된 eps == 게이트가 쓴 eps, desync 불가). 결정 1("validation.json에 eps 영속")의 충실한 구현.

## 구현 3: validation.json·retrain.json 원자 co-visible 영속 (결정 1·2·7) — `deploy.py`

`materialize()` 시그니처에 `validation` 추가, 두 JSON을 쓰고 **ready 마커로 원자 완성**:
```python
def materialize(retrain_result, version, *, validation, root=ARTIFACTS):
    ...  # 기존 model.pt/pre.npz/meta.json/reference.npz 기록 후
    import dataclasses, os, time
    rr, out = retrain_result, root / f"gru_{rr.featureset}@{version}"

    val = {**dataclasses.asdict(validation),                       # eps 포함(구현 3-pre)
           "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}  # UTC, Z 접미사(mn5)
    rj  = {"epochs": rr.epochs, "val_loss": rr.val_loss, "b_split_seed": rr.seed,
           "n_train_pids": len(rr.train_pids), "n_b_retrain": len(rr.b_retrain),
           "n_b_holdout": len(rr.b_holdout), "run_id": rr.run_id, "git_commit": rr.git_commit}

    _atomic_write_json(out / "validation.json", val)   # temp → os.replace
    _atomic_write_json(out / "retrain.json", rj)        # temp → os.replace
    _atomic_write_json(out / ".ready", {"complete": True})  # 마지막, AND 완성 표식
    return out
```
**원자 기법**:
```python
def _atomic_write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)   # 원자 rename — torn read 방지
```
- **완성 표식 = `.ready` 존재** (두 파일 AND의 구현). 콘솔은 `.ready` 없으면 *미완성 후보*로 배제(decisions 결정 7). `.ready`를 **마지막에** 쓰므로, validation.json·retrain.json 둘 다 완전히 보일 때만 `.ready`가 보인다 → "하나만/torn" 중간 상태 비노출.
- **mn2 (콘솔 정렬 — 교차계약)**: 콘솔 design은 완성 표식을 "두 파일 AND"로 적었고 여기선 `.ready` 존재로 구현했다. 콘솔 명세부는 완성 판정을 **`.ready` 존재**로 검사하도록 정렬해야 한다(둘 다 fail-closed). 범위 외(콘솔)지만 교차 계약으로 명시.
- `validated_at`는 UTC(`time.gmtime()`)에 `Z` 접미사로 타임존 명시(mn5) — 다중 환경 감사 모호성 제거.

### 호출부 갱신 + 항상-materialize 불변 (MJ-b)
- **기존 호출부 갱신**: `validation`이 keyword-only 필수가 되므로 `scripts/h4r_c_smoke.py:51`을
  `deploy.materialize(rr, "v1-retrain", root=ROOT)` → `deploy.materialize(rr, "v1-retrain", validation=vr, root=ROOT)`로 고친다(`vr`은 직전 `validate.validate(rr, old)` 결과).
- **불변 — 항상 materialize**: `materialize`는 **게이트 통과/실패와 무관하게 항상 호출**한다(REGRESSED 버전도 challenger로 영속·표시돼야 콘솔이 게이트 결과를 보여줄 수 있다). 게이트 강제는 `materialize`가 아니라 `swap`에서만 일어난다(deploy.py:61, `no_regression` 미통과 시 swap raise).

## 구현 4: 서빙 alias reload + 드리프트 정합 (결정 5·6, B1) — `serve/app.py`

> **B1 핵심**: `_S`(번들)만 alias로 옮기고 `_DS`(drift reference)를 방치하면 살아있는 `drift_state()`(app.py:97-110)·`drift_endpoint()`(app.py:113-122)와 어긋나 /drift 흐름이 끊긴다. `_load_all`이 **drift가 읽는 스키마 그대로** `_DS`를 채우고, `drift_state()`도 같은 alias 경로로 라우팅해 모델↔reference를 동일 version_dir로 묶는다.

**임포트 추가 (mn3)** — 현재 app.py는 `from sepsis.serve.bundle import load_bundle`, `from sepsis.drift import reference as R, run as drift_run, synthetic`만 가진다:
```python
import threading
from sepsis.serve.bundle import load_bundle, load_bundle_from_dir   # load_bundle_from_dir 추가
# R.load_reference · synthetic.calibrate · C · Path는 이미 임포트됨(app.py:14,17,22,23)
```

### (a) 번들 소스를 alias로 통일 + ARTIFACTS 절대경로 (결정 5, MJ-e)
```python
ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", str(C.ROOT / "deploy" / "artifacts")))
# deploy.py:25 ARTIFACTS = C.ROOT/"deploy"/"artifacts"(절대)와 단일 진실원천 공유.
# 상대 "deploy/artifacts"는 cwd 의존 → 컨테이너에서 deploy가 쓴 alias와 어긋남(MJ-e) → 금지.

_LOCK = threading.Lock()

def _resolve_alias(fs: str) -> Path:
    return (ARTIFACTS / f"gru_{fs}").resolve()   # alias symlink 1회 해석 → 고정 version dir

def state() -> dict:
    if "pred" not in _S:
        _load_all(_resolve_alias(os.environ.get("SERVE_FEATURESET", "vitals")))
    return _S
```
- 기존 `SERVE_BUNDLE_DIR` 고정 경로 / dev MLflow 폴백은 **alias 통일로 superseded**(결정 5/mn3). **`SERVE_BUNDLE_DIR` 분기는 `state()`·`drift_state()` 양쪽에서 제거** — 둘 다 같은 alias만 본다(dev·컨테이너 단일 소스).

### (b) `_load_all` — `_S`·`_DS`를 같은 version_dir에서 동시 구성 + 원자 재바인딩 (결정 6, B1, MJ-d)
```python
def _load_all(version_dir: Path) -> None:
    global _S, _DS
    with _LOCK:                                    # writer 직렬화(동시 reload/swap 배제)
        b = load_bundle_from_dir(version_dir)
        ref = R.load_reference(version_dir / "reference.npz")   # ★ 파일 경로(.npz) — 디렉토리 아님
        wn = int(os.environ.get("DRIFT_WINDOW_N", "500"))
        nt = int(os.environ.get("DRIFT_CAL_TRIALS", "300"))
        new_s = {"bundle": b, "pred": StatefulPredictor(b),
                 "cols": C.featureset_columns(b.featureset)}
        new_ds = {"ref": ref,                                   # ★ drift_endpoint가 읽는 스키마 그대로
                  "thr": synthetic.calibrate(ref, window_n=wn, n_trials=nt),
                  "min_patients": wn}
        _S = new_s          # 단일 이름 재바인딩(원자) — 리더는 옛 dict 또는 새 dict, 부분상태 없음
        _DS = new_ds        # 〃
```
- **`_DS` 스키마 = `ref`·`thr`·`min_patients`** — 살아있는 `drift_endpoint`가 읽는 그대로(app.py:118·120·121). 옛 핸드오프의 `reference` 키는 drift가 소비하지 않는 죽은 코드였다 → 교정(B1-1).
- **`load_reference`는 `R.load_reference`**(app.py 임포트명, bare `load_reference`는 미정의)이며 인자는 `version_dir/"reference.npz"` **파일 경로**(reference.py:83 `np.load(path)`는 .npz 파일). 디렉토리를 넘기면 즉시 예외(B1-2).
- **원자 가시성 (MJ-d)**: `_S.clear()/update()`의 2단계(torn) 대신 **새 dict를 lock 안에서 완성한 뒤 모듈 전역 이름을 1회 재바인딩**(`global _S, _DS`). CPython 이름 재바인딩(STORE_NAME)은 GIL 하 단일 연산이라 리더(`state()`/`drift_state()`가 반환한 dict를 잡은)는 옛 스냅샷 또는 새 스냅샷만 본다 — transient KeyError 없음. **리더는 락 불필요**(요청 동안 잡은 dict 참조가 일관 스냅샷).
- **스큐 없음 (결정 6)**: `_S`·`_DS` 모두 같은 `version_dir`에서 파생. 두 재바인딩 사이 1 바이트코드 창에서 `/predict`(새 _S)·`/drift`(옛 _DS)가 갈릴 수 있으나, 각 엔드포인트는 둘 중 하나만 읽고 새 _DS도 같은 version_dir 산이라 모델-reference 짝은 **항상 동일 버전**. (단일 dict 통합은 대규모 리팩터라 범위 외 — 결정 6의 "동일 버전" 요구는 충족.)

### (c) thr 재캘리브레이션 정책 (B1)
- **모든 `_load_all`(부팅·첫 요청·`/admin/reload`)은 새 reference로 thr를 재캘리브레이션**한다. `thr`는 reference별(`ref.summary` 부트스트랩, synthetic.py:56-70)이라, 재학습 reference(A-train+B-retrain)에 옛 thr를 재사용하면 거짓 드리프트 → 정확성 버그. 따라서 reference와 thr는 **항상 한 묶음으로** 갱신.
- **비용**: `n_trials` 기본 300(`DRIFT_CAL_TRIALS`로 조정, dev는 낮춤). 캘리브레이션은 `_LOCK` 안·재바인딩 **전**에 끝나므로 리더는 그동안 옛 스냅샷으로 계속 서빙(블로킹 없음). 프로덕션 롤링 경로는 pod 부팅 시(readiness 전) 1회 부담 — 수용.

### (d) `drift_state()` 교체 — alias 정합 (B1-3)
살아있는 `drift_state()`(app.py:97-110)의 옛 lazy 경로(`SERVE_BUNDLE_DIR`/`build_reference`)는 superseded. `state()`와 **동일하게 `_load_all`로 라우팅**:
```python
def drift_state() -> dict:
    if "ref" not in _DS:
        _load_all(_resolve_alias(os.environ.get("SERVE_FEATURESET", "vitals")))
    return _DS
```
- `/drift`가 `/predict`보다 먼저 와도 같은 alias·같은 version_dir에서 `_S`·`_DS`를 함께 로드 → drift baseline = **활성 alias 번들의 `reference.npz`**(= 모델과 동일 버전). 결정 6의 스큐 방지가 실제로 닫힌다(B1-3).
- `drift_endpoint()`(app.py:113-122)는 `ds["ref"]·ds["thr"]·ds["min_patients"]`를 읽으므로 **수정 불필요** — 새 `_DS` 스키마가 그 계약을 그대로 만족.

### (e) dev in-place reload 엔드포인트
```python
@app.post("/admin/reload")
def admin_reload():
    fs = os.environ.get("SERVE_FEATURESET", "vitals")
    _load_all(_resolve_alias(fs))     # alias 재해석 → 새 활성 버전(번들+reference+thr) 로드
    return {"reloaded": True, "version_dir": _resolve_alias(fs).name}
```

### (f) 프로덕션 전파 = K8s 롤링 재시작 (콘솔이 트리거, 서빙 코드 변경 없음)
- 콘솔이 alias swap 후 Deployment patch(롤링 재시작) → 새 pod 부팅 시 첫 요청이 `state()`/`drift_state()`로 `_load_all` 1회 → fresh 로드. 버전 교체 시 hidden state 리셋이 **올바름**(새 모델엔 새 상태).
- 부팅 시 1회 해석이라 스큐 구조적 부재 → 원자 재바인딩 락은 dev in-place reload 경로 보호용.

### (g) 전파 확인 식별자 (MJ2)
`/health.run_id`는 `meta.json.run_id`를 반환한다(구현 2 이후). 콘솔의 전파 확인은 `/health.run_id`를 **현재 alias 타겟 dir의 `meta.json.run_id`**와 비교(`active_version` 디렉토리명 문자열과 비교 아님).

---

## 성공 기준 (spec-writer TDD 대상)

1. `RetrainResult`에 `run_id·git_commit·seed`가 채워진다. `seed`가 `retrain.json.b_split_seed`에 도달한다(MJ1). 재학습 run이 `sqlite:///{C.ROOT}/mlflow.db`에 기록돼 `run_id`로 조회된다(MJ-a). `git_commit`은 더티 트리에 `+dirty`, non-repo에 `"unknown"`을 반환한다(mn1).
2. `meta.json`에 `run_id`가 기록되고, `/health.run_id`가 alias명이 아닌 실제 run_id를 반환한다(MJ2).
3. version dir에 `validation.json`·`retrain.json`이 기록되고, **둘 다 완전할 때만 `.ready`가 존재**한다. `.ready` 없는 dir은 미완성(결정 7). `validation.json.eps`가 게이트에 실제 사용된 eps와 일치하고(MJ-c), `validated_at`은 UTC `Z` 표기다(mn5).
4. 각 JSON은 `os.replace`로 원자 기록되어 부분/torn read가 없다. `materialize`는 게이트 통과/실패 무관하게 호출돼 REGRESSED 버전도 challenger로 영속된다(MJ-b).
5. `state()`가 alias(`gru_<fs>`)를 해석해 로드한다(ARTIFACTS 기본값 = `C.ROOT/deploy/artifacts` 절대경로, MJ-e). `/admin/reload` 후 새 활성 버전이 반영된다.
6. `_S`·`_DS`가 동일 `version_dir`에서 로드된다(스큐 없음). `_DS` 스키마는 `ref·thr·min_patients`로 `drift_endpoint`가 읽는 그대로다(B1-1). 로드는 `_LOCK`으로 직렬화되고, `_S`·`_DS`는 **원자 재바인딩**돼 리더가 부분상태를 보지 않는다(MJ-d).
7. **drift 정합(B1)**: `/drift`가 `/predict`보다 먼저 와도 `drift_state()`가 활성 alias 번들의 `reference.npz`를 baseline으로 쓴다(SERVE_BUNDLE_DIR/build_reference 회귀 없음). `_load_all`은 새 reference로 thr를 재캘리브레이션한다(B1-c). `R.load_reference`에 `reference.npz` **파일** 경로가 전달된다(B1-2).
8. **누수 불변**: 위 변경이 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF를 건드리지 않는다(영속·로딩 계층 한정).

## 범위 외 (명시)

- 콘솔 API/UI, 감사 DB, FS↔감사 DB 일관성 → 콘솔 작업.
- 정확한 K8s RBAC·Deployment patch 매니페스트 → 배포환경 종속 `[검증 필요]`.
- `deploy.rollback`의 `approved` 가드·prev 반환 대칭화 → 콘솔이 사전 `active_version` 읽기로 우회(비블로킹), 콘솔 문서 권고로 보유.