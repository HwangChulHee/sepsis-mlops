# Console-Prep 구현 핸드오프 (명세부) — H4 백엔드 보강

> **전제**: `design/console-prep/decisions.md`(설계부) 2라운드 검토 통과(blocker 0). 본 문서는 그 결정 1~7의 *구현 방법*을 자립형으로 명세한다. 설계 근거는 decisions.md 참조.
> **워크플로우**: 검토(`handoff_review.md`) 통과 → spec-writer TDD → 구현.
> **대상 파일**: `src/sepsis/retrain/{pipeline.py, deploy.py}`, `src/sepsis/serve/app.py`. (콘솔 API/UI·FS↔DB 일관성은 범위 외.)

## 코드 현황 (구현 시작점)

- `RetrainResult`(pipeline.py:31-47): `featureset·input_dim·hp·tau·stats·model·b_retrain·b_holdout·train_pids·epochs·val_loss·mask_on` **있음**. `run_id·git_commit·seed` **없음**.
- `retrain()`(pipeline.py:50): `seed` 인자로 받으나 `RetrainResult`에 전달 안 함. 끝에서 `RetrainResult(...)` 생성(pipeline.py:94-98).
- `ValidationResult`(validate.py:31-43): `bholdout_util·bholdout_prauc·new_aval_util·old_aval_util·new_aval_prauc·old_aval_prauc·no_regression·cross_site_claim·distribution·note`. `eps`·timestamp **없음**(주입 대상).
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

`retrain()`을 MLflow run으로 감싸고 3종 주입:
```python
import mlflow, subprocess
# retrain() 본문을 with mlflow.start_run() as run: 로 감싼다
run_id = run.info.run_id
git_commit = subprocess.run(["git","rev-parse","HEAD"], capture_output=True, text=True).stdout.strip()
mlflow.log_params({"featureset": featureset, "seed": seed, **hp})
mlflow.log_metrics({"epochs": res.n_epochs, "val_loss": res.best_val_loss})
# RetrainResult(...) 생성 시 run_id=run_id, git_commit=git_commit, seed=seed 추가
```
- **git_commit 용도** = 감사·MLflow 링크용 식별자. **재현은 `seed` 몫**(git_commit은 재현 키 아님). 더티 트리면 `+dirty` 접미사 허용.

## 구현 2: `meta.json`에 run_id (결정 4) — `deploy.py` `materialize()`

`meta.json` dict에 `run_id` 추가:
```python
{"featureset": rr.featureset, "hp": rr.hp, "input_dim": rr.input_dim,
 "tau": rr.tau, "version": version, "trained_on": "A-train+B-retrain",
 "run_id": rr.run_id}   # ADDED — MLflow 연결 키의 단일 권위 출처
```
- `load_bundle_from_dir`의 `meta.get("run_id", str(d.name))` 폴백(bundle.py:102)이 이제 실제 run_id를 받아 `/health` 식별 정상화.

## 구현 3: validation.json·retrain.json 원자 co-visible 영속 (결정 1·2·7) — `deploy.py`

`materialize()` 시그니처에 `validation` 추가, 두 JSON을 쓰고 **ready 마커로 원자 완성**:
```python
def materialize(retrain_result, version, *, validation, root=ARTIFACTS):
    ...  # 기존 model.pt/pre.npz/meta.json/reference.npz 기록 후
    import dataclasses, os, time
    rr, out = retrain_result, root / f"gru_{rr.featureset}@{version}"

    val = {**dataclasses.asdict(validation), "eps": 0.02,
           "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
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
- `eps`는 `ValidationResult` 필드 아니므로 영속 시 주입(현재 기본 0.02; `validate(*, eps=)` 인자와 일치 유지).

## 구현 4: 서빙 alias reload — 프로덕션 롤링 + dev reload 둘 다 (결정 5·6) — `serve/app.py`

**(a) 번들 소스를 alias로 통일** — `state()` 수정:
```python
ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", "deploy/artifacts"))

def _resolve_alias(fs: str) -> Path:
    return (ARTIFACTS / f"gru_{fs}").resolve()   # alias 1회 해석 → 고정 버전 dir

def state() -> dict:
    if "pred" not in _S:
        _load_all(_resolve_alias(os.environ.get("SERVE_FEATURESET", "vitals")))
    return _S
```
- 기존 `SERVE_BUNDLE_DIR` 고정 경로 / dev MLflow 폴백은 **alias 통일로 대체(superseded)** — dev·컨테이너 양쪽이 같은 alias를 본다(decisions mn3).

**(b) reload 원자성** — `_S`·`_DS`를 **같은 해석 시점**으로 동시 로드(결정 6):
```python
import threading
_LOCK = threading.Lock()

def _load_all(version_dir: Path):
    with _LOCK:                       # 로드 중 swap/reload 배제(직렬화)
        b = load_bundle_from_dir(version_dir)
        _S.clear(); _S.update(bundle=b, pred=StatefulPredictor(b),
                              cols=C.featureset_columns(b.featureset))
        _DS.clear(); _DS.update(reference=load_reference(version_dir))  # 동일 버전 dir
```
- `_S`(첫 `/predict`)·`_DS`(첫 `/drift`)가 **같은 `version_dir`**에서 로드 → 모델↔reference 스큐 없음.

**(c) dev in-place reload 엔드포인트**:
```python
@app.post("/admin/reload")
def admin_reload():
    fs = os.environ.get("SERVE_FEATURESET", "vitals")
    _load_all(_resolve_alias(fs))     # alias 재해석 → 새 활성 버전 로드
    return {"reloaded": True, "version_dir": str(_resolve_alias(fs).name)}
```

**(d) 프로덕션 전파 = K8s 롤링 재시작** (콘솔이 트리거, 서빙 코드 변경 없음):
- 콘솔이 alias swap 후 Deployment를 patch(롤링 재시작) → 새 pod 부팅 시 `state()`가 alias 1회 해석·fresh 로드. 버전 교체 시 hidden state 리셋이 **올바름**(새 모델엔 새 상태).
- 롤링 경로는 부팅 시 1회 해석이라 스큐 구조적으로 없음 → 원자성 락은 dev in-place reload 경로에만 필요.

**(e) 전파 확인 식별자 (MJ2)** — `/health.run_id`는 `meta.json.run_id`를 반환한다(구현 2 이후). 콘솔의 전파 확인은 `/health.run_id`를 **현재 alias 타겟 dir의 `meta.json.run_id`**와 비교(`active_version` 디렉토리명 문자열과 비교 아님).

---

## 성공 기준 (spec-writer TDD 대상)

1. `RetrainResult`에 `run_id·git_commit·seed`가 채워진다. `seed`가 `retrain.json.b_split_seed`에 도달한다(MJ1).
2. `meta.json`에 `run_id`가 기록되고, `/health.run_id`가 alias명이 아닌 실제 run_id를 반환한다(MJ2).
3. version dir에 `validation.json`·`retrain.json`이 기록되고, **둘 다 완전할 때만 `.ready`가 존재**한다. `.ready` 없는 dir은 미완성(결정 7).
4. 각 JSON은 `os.replace`로 원자 기록되어 부분/torn read가 없다.
5. `state()`가 alias(`gru_<fs>`)를 해석해 로드한다. `/admin/reload` 후 새 활성 버전이 반영된다.
6. `_S`·`_DS`가 동일 `version_dir`에서 로드된다(스큐 없음). 로드는 `_LOCK`으로 직렬화된다.
7. **누수 불변**: 위 변경이 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF를 건드리지 않는다(영속·로딩 계층 한정).

## 범위 외 (명시)

- 콘솔 API/UI, 감사 DB, FS↔감사 DB 일관성 → 콘솔 작업.
- 정확한 K8s RBAC·Deployment patch 매니페스트 → 배포환경 종속 `[검증 필요]`.
- `deploy.rollback`의 `approved` 가드·prev 반환 대칭화 → 콘솔이 사전 `active_version` 읽기로 우회(비블로킹), 콘솔 문서 권고로 보유.