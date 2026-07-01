# Console 구현 핸드오프 A (명세부) — 백엔드: 감사 ORM + `/console` API + 트랜잭션 경계

> **전제**: `design/console/decisions.md`(설계부) 2라운드 검토 통과. 본 문서는 결정 4·5·5-A·5-B·6-A·7의 *구현 방법*을 자립형으로 명세한다. 설계 근거는 decisions.md 참조.
> **워크플로우**: 검토(`handoff_backend_review.md`) 통과 → spec-writer TDD → 구현. 프론트(React)·관측(Grafana)은 **핸드오프 B**(별도 문서).
> **대상 파일(신규)**: `src/sepsis/console/{__init__.py, audit.py, service.py, api.py}`. **재활용(변경 없음)**: `retrain/deploy.py`, `retrain/validate.py`, `serve/bundle.py`.
> **선행(교차단계, 이미 닫힘)**: console-prep으로 `validation.json`·`retrain.json`·`.ready` 영속, `meta.json.run_id`, 서빙 `/admin/reload`·`/health.run_id`가 모두 구현됨 — 본 핸드오프는 그 위에 얹는다.
> **상태**: 명세부 v3 — R2 PASS + `versions` 계약 명문화("구현 2 보강" 절) + archived 버킷 규약 확정 + **B1·B2·MJ1·MJ2 복원**(v2 doc 편집이 라운드1 식별자-규약 수정을 되돌렸던 것을 재적용 — 버전 식별자=디렉토리명 단일화, 이중접두 제거, git_commit→retrain.json, do_orm_execute bulk 차단). 테스트는 R2 PASS 시점 규약을 인코딩하므로 본 복원과 정합.

## 코드 현황 (구현 시작점 — 재활용 대상의 실제 시그니처)

- `deploy.active_version(featureset, *, root=ARTIFACTS) -> str | None` (deploy.py:71): alias가 가리키는 **버전 디렉토리명** 반환(`os.readlink`, 락 없음). alias 없으면 None.
- `deploy.swap(featureset, version_dir, *, validation, approved, root=ARTIFACTS) -> str | None` (deploy.py:80): `approved is not True`면 `PermissionError`, `validation.no_regression` falsy면 `ValueError`. 통과 시 alias 전환 후 **이전 활성 버전명**(prev) 반환. `validation`·`approved`는 **keyword-only**.
- `deploy.rollback(featureset, previous_version_name, *, root=ARTIFACTS) -> None` (deploy.py:93): alias만 되돌림. **승인 가드·prev 반환·감사 훅 없음**(API 경계가 보강).
- `bundle.set_alias(root, alias, target_name)` (bundle.py:24): 상대 심링크 `os.replace` 원자 스왑.
- `ValidationResult`(validate.py:31): `bholdout_util·bholdout_prauc·new_aval_util·old_aval_util·new_aval_prauc·old_aval_prauc·no_regression·cross_site_claim·distribution·note·eps`. swap이 보는 하드 게이트 = `no_regression`(bool).
- 서빙 `GET /health`(app.py:105) → `{"status","run_id","featureset","input_dim"}`. `run_id` = 활성 번들 `meta.json.run_id`(console-prep로 실제 run_id 보고).
- 서빙 `POST /admin/reload`(console-prep) → alias 재해석, `_S`/`_DS` 재로드.
- `ARTIFACTS = C.ROOT / "deploy" / "artifacts"` (deploy.py:27). version dir = `gru_<fs>@<version>`, 완성 표식 = `.ready` 존재.

---

## 버전 식별자 규약 (B1·B2 — 단일 표현 못박기)

> 본 규약은 결정 5·7의 구현 명세다(새 설계 결정 아님). 핸드오프 전 구간이 **하나의 표현**만 쓰게 해 화해 비교·롤백 타겟·archived 링크·전파 타겟이 어긋나지 않게 한다.

**규약 — 정규 식별자 = 버전 디렉토리명**: 감사 `from_version`/`to_version`, 화해(`_reconcile_or_seed`) 비교, 롤백 타겟, 전파 타겟, 그리고 쓰기 API의 버전 입력은 **전부 버전 디렉토리명** `gru_<fs>@<v>`(예: `gru_vitals@v3`) **하나로 통일**한다. 맨버전(`v3`)은 핸드오프 어디에도 **단독으로 등장하지 않는다.** (단, 읽기 API `list_versions` *응답 JSON*의 `version`/`active` 필드는 행 표시용으로 접두를 떼낸 표현을 노출 — 내부 식별자와 구분, "구현 2 보강" 절.)

**근거(코드 정합)**: `deploy.active_version(fs)`는 `os.readlink`로 **디렉토리명**을 반환하고(deploy.py:71-73), `deploy.rollback`/`deploy.set_active`→`bundle.set_alias`는 그 문자열을 **상대 심링크 타겟으로 그대로** 쓴다(bundle.py:35, deploy.py:77·95). 즉 alias 계약의 양끝이 이미 디렉토리명이므로 그 표현으로 통일하면 화해·롤백·전파에 **변환이 0회**다.

**경로 구성 헬퍼(단일화)**:
```python
def _version_dir(version_id: str) -> Path:    # version_id = "gru_vitals@v3" (디렉토리명)
    return ARTIFACTS / version_id             # 접두사 재부착 금지 — B2 이중접두사 차단

def _require_consistent(fs: str, version_id: str) -> None:
    # version_id가 이 featureset 소속인지 가드(교차 fs 오승인 차단 → API 422)
    if not version_id.startswith(f"gru_{fs}@"):
        raise ValueError(f"version {version_id!r} not in featureset {fs!r}")
```

- **금지**: `ARTIFACTS / f"gru_{fs}@{...}"` 형태의 접두사 재조립은 본 핸드오프 전 구간에서 금지하고 `_version_dir(version_id)`만 쓴다.

---

## 구현 1: 감사 저장소 (SQLAlchemy ORM) — `console/audit.py`

결정 4. **신규 의존**(레포 `src/`에 SQLAlchemy 직접 사용 0건 — MLflow 전이 의존일 뿐, M1). SQLite 시작, PostgreSQL 교체 가능하게 engine URL만 주입.

### 스키마 (단일 테이블 `audit_events`)

```python
class AuditEvent(Base):
    __tablename__ = "audit_events"
    id            = Column(Integer, primary_key=True)            # 자동 증가
    ts            = Column(DateTime, nullable=False)             # UTC, 전용칸(검색)
    event_type    = Column(String, nullable=False)              # 전용칸(검색): APPROVE|ROLLBACK|RECONCILE|BOOTSTRAP
    featureset    = Column(String, nullable=False)              # 전용칸: 직렬화 키 단위(deploy.py:71)
    gate_passed   = Column(Boolean, nullable=True)              # 전용칸(검색): no_regression. ROLLBACK/RECONCILE/BOOTSTRAP은 NULL
    from_version  = Column(String, nullable=True)               # prev(이전 활성), 콜드스타트 BOOTSTRAP은 NULL
    to_version    = Column(String, nullable=False)              # 대상(새 활성)
    run_id        = Column(String, nullable=True)               # 메모상자: MLflow 링크 키(박제). 없으면 NULL
    git_commit    = Column(String, nullable=True)               # 메모상자
    gate_snapshot = Column(JSON,   nullable=True)               # 메모상자: validation.json 통째 사본(박제)
    actor_unverified = Column(String, nullable=False, default="operator")  # M4: 미검증 입력 명시
    verified_subject = Column(String, nullable=True)            # M4: SSO/OIDC 예약, MVP는 NULL
    reason        = Column(String, nullable=False, default="")  # 자유 텍스트(롤백 사유 등)
```

- **전용칸 vs 메모상자 (핑퐁 확정)**: 자주 필터·검색하는 `ts`·`event_type`·`gate_passed`는 컬럼으로, 나머지 게이트 수치(`bholdout_util` 등)는 `gate_snapshot` JSON에 통째 박제. `validation.json` 스키마가 바뀌어도 감사가 안 깨진다.
- **게이트 스냅샷 출처 = 디스크 (N1)**: 휘발성 `ValidationResult`가 아니라 **승인 대상 version dir의 `validation.json`을 읽어 그 사본**을 박는다(결정 4·5-B). `.ready` 없는 dir은 애초에 승인 대상이 아니라 빈 스냅샷이 생길 일 없음.
- **`actor` = 미검증 입력 (M4)**: MVP에 인증 없음. 필드명을 `actor_unverified`로 둬 "검증된 신원" 오주장 방지. `verified_subject`는 SSO 도입용 예약 컬럼(MVP NULL).

### append-only 강제 (불변)

의료 감사는 정정·삭제가 신뢰 붕괴. **application 레벨에서 UPDATE/DELETE 차단** — 정정도 새 레코드 추가로만. **두 경로 모두** 막아야 한다 (MJ2): ORM unit-of-work(`session.dirty/deleted`)와 **bulk 경로**(`Query.update()`/`delete()`, `Session.execute(update(...)/delete(...))`)는 서로 다른 훅을 탄다 — `before_flush`는 bulk를 못 본다.

```python
from sqlalchemy import event, update, delete
from sqlalchemy.orm import Session

# (1) ORM unit-of-work 경로 — INSTANCE UPDATE/DELETE 차단
@event.listens_for(Session, "before_flush")
def _block_uow_mutation(session, ctx, _):
    if session.dirty or session.deleted:
        raise PermissionError("audit_events is append-only (no instance UPDATE/DELETE)")

# (2) bulk 경로 — Query/Core UPDATE·DELETE 차단(before_flush 우회 구멍 봉쇄, MJ2)
@event.listens_for(Session, "do_orm_execute")
def _block_bulk_mutation(state):
    if state.is_update or state.is_delete:        # bulk update()/delete() statement
        raise PermissionError("audit_events is append-only (no bulk UPDATE/DELETE)")
```

- **두 훅의 분담**: (1)은 로드한 인스턴스를 고쳐 flush하는 경로, (2)는 `session.execute(update(AuditEvent)...)`·`delete(AuditEvent)...`·`query.update()/delete()` 같은 unit-of-work **우회** 경로. 둘을 함께 걸어야 append-only가 실제 불변이 된다.
- **DB 레벨 강제([검증 필요])**: PostgreSQL 승격 시 `BEFORE UPDATE/DELETE` 트리거 거부 또는 앱 롤에서 UPDATE/DELETE 권한 회수로 **DB 레벨에서도** 이중화 권고. MVP(SQLite)는 위 두 ORM 훅으로 닫는다.

### 인터페이스 (서비스 계층이 부르는 얇은 API)

```python
class AuditStore:
    def __init__(self, url: str = "sqlite:///console_audit.db"): ...   # engine/sessionmaker
    def append(self, **fields) -> AuditEvent: ...                      # 단일 INSERT, ts 미지정시 utcnow
    def last_active(self, featureset: str) -> AuditEvent | None: ...   # 최근 APPROVE/ROLLBACK/RECONCILE/BOOTSTRAP 1건
    def query(self, *, event_type=None, gate_passed=None,
              since=None, until=None, featureset=None) -> list[AuditEvent]: ...  # 전용칸 필터
```

- `last_active`는 화해(구현 3)와 `/audit` 최신 상태 표시의 공통 헬퍼. **현재 활성의 권위는 DB가 아니라 alias**임에 유의(결정 7-2) — `last_active`는 *감사상* 최종 활성일 뿐.

---

## 구현 2: `/console` API (FastAPI, 5 엔드포인트) — `console/api.py` + `console/service.py`

결정 5. **얇은 어댑터** — `deploy`/`validate`/`bundle` 함수를 호출만, 로직 재구현 0. API(`api.py`)는 HTTP 표면, 직렬화·감사·복원의 실제 로직은 서비스 계층(`service.py`)에 둬 테스트 가능하게 분리.

### 읽기 3개

| 엔드포인트 | 동작 | 출처 |
|---|---|---|
| `GET /console/versions?fs=vitals` | version dir 스캔 → `champion`(=alias 타겟) / `challenger`(`.ready` 있고 비활성) / `incomplete`(`.ready` 없음) / `archived`(과거 활성, 감사 이력) 분류 | **FS가 권위**: champion = `active_version()`. archived 도출 = 감사 `last_active` 이력 |
| `GET /console/versions/{version}?fs=vitals` | 그 dir의 `validation.json`·`retrain.json`·`meta.json` 읽어 게이트 수치 + 재학습 상세 + MLflow deep-link | 읽기 전용. `meta.json.run_id` 있으면 링크, 없으면 폴백 표시(6-A) |
| `GET /console/audit?event_type=&gate_passed=&since=` | `AuditStore.query` 전용칸 필터 | DB |

- **버전 단위 = featureset (결정 5)**: `active_version`이 featureset 단위라 모든 엔드포인트가 `fs` 파라미터를 받는다. champion 판정은 항상 `deploy.active_version(fs)`(alias)로 — 감사 DB로 추정하지 않는다.
- **MLflow deep-link (6-A)**: `meta.json.run_id`가 있으면 `<tracking_uri>/#/experiments/.../runs/<run_id>`. 없으면 죽은 링크 만들지 말고 `validation.json`/`retrain.json` 내용을 직접 표시.

### 쓰기 2개 — 직렬화 경계 + 감사 강제 (결정 5-A·7)

두 쓰기 모두 **featureset 단위 임계 구간** 안에서 `read-active(prev) → 백엔드 호출 → audit` 순서로 실행. 임계 구간은 동시 승인 2건의 prev 갈라짐을 차단(결정 7-1).

```python
# service.py — featureset 단위 락(콘솔 1프로세스 전제 → 프로세스-로컬 락)
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

def approve(fs: str, version_id: str, *, actor: str, reason: str = "") -> dict:
    _require_consistent(fs, version_id)               # version_id = "gru_<fs>@<v>" 디렉토리명, 교차-fs 가드(422)
    version_dir = _version_dir(version_id)            # ARTIFACTS / version_id — 접두사 재부착 없음(B2)
    _require_ready(version_dir)                       # .ready 없으면 422 — 미완성 후보 승인 거부
    val = _restore_validation(version_dir)            # validation.json → SimpleNamespace (5-B)
    snap = _read_gate_snapshot(version_dir)           # validation.json 사본(박제용)
    meta = _read_meta(version_dir)                    # run_id(meta.json, deploy.py:52)
    retr = _read_retrain(version_dir)                 # git_commit(retrain.json, deploy.py:64 — MJ1)
    with _LOCKS[fs]:                                  # ── 임계 구간 시작(결정 7-1) ──
        prev = deploy.active_version(fs)              # 임계 구간 안에서 prev 읽기(디렉토리명, mn-c)
        deploy.swap(fs, version_dir, validation=val, approved=True)   # ② 미승인/REGRESSED면 raise
        ev = audit.append(event_type="APPROVE", featureset=fs,
                          gate_passed=bool(snap.get("no_regression")),
                          from_version=prev, to_version=version_id,    # 둘 다 디렉토리명(B1)
                          run_id=meta.get("run_id"), git_commit=retr.get("git_commit"),  # MJ1
                          gate_snapshot=snap, actor_unverified=actor, reason=reason)  # ③
    # ── 임계 구간 끝 ── (서빙 전파는 구간 밖, 아래 전파 확인)
    propagation = _propagate_and_confirm(fs)          # /admin/reload | 롤링 재시작 + /health 폴링
    return {"event_id": ev.id, "prev": prev, "active": version_id, "propagation": propagation}

def rollback(fs: str, target_version_id: str, *, actor: str, reason: str = "") -> dict:
    # 5-A: 롤백도 승인+감사 필수, 단 validation 재검증 면제(과거 검증된 버전 복귀)
    _require_consistent(fs, target_version_id)        # target_version_id = "gru_<fs>@<v>" 디렉토리명(B1)
    with _LOCKS[fs]:
        prev = deploy.active_version(fs)              # 롤백 prev = 사전 읽기(deploy.rollback이 prev 미반환, mn-c)
        deploy.rollback(fs, target_version_id)         # 디렉토리명을 그대로 심링크 타겟으로(bundle.py:35) — validation 게이트 없음(의도)
        ev = audit.append(event_type="ROLLBACK", featureset=fs, gate_passed=None,
                          from_version=prev, to_version=target_version_id,
                          actor_unverified=actor, reason=reason)
    propagation = _propagate_and_confirm(fs)
    return {"event_id": ev.id, "prev": prev, "active": target_version_id, "propagation": propagation}
```

- **swap 복원 경로 (5-B)**: `_restore_validation`은 `validation.json`을 읽어 `SimpleNamespace(**data)`로 래핑. `deploy.swap`이 `getattr(validation,"no_regression",...)`로 속성 접근하므로 dict가 아닌 객체 필요. **백엔드 시그니처 변경 없음.**
- **이중 게이트 (M3)**: REGRESSED(`no_regression=False`) 버전은 (a) UI가 승인 버튼 비활성(핸드오프 B), (b) 백엔드 `deploy.swap`이 `ValueError`로 거부 — 양쪽에서 막힘. API는 `ValueError`를 422로 변환.
- **순서 = swap(②) → audit(③) (결정 7-2)**: ③ 실패는 부팅 화해(구현 3)가 받친다. 역순(기록 먼저)은 "기록 있는데 swap 안 됨" 창을 만들어 alias 권위와 충돌하므로 금지.
- **`deploy.rollback` 직접 호출 우회 (5-A 방어 심화)**: H4r에 `approved` 가드를 더하는 건 콘솔 밖 변경이라 권고로만 둠. 콘솔은 사전 `active_version` 읽기로 prev를 감사에 캡처해 우회.

---

## 구현 3: 부트스트랩 화해·seed + 전파 확인 — `console/service.py` (lifespan)

결정 7-1·7-2, 결정 2-A. **요청 수락 *전에*** alias↔감사를 화해하고, 쓰기 후 서빙 전파를 확인한다.

### (a) 부트스트랩: 화해/seed 후 라우팅 개시 (결정 7-1 경계 완전성)

FastAPI `lifespan`에서 라우팅 게이트가 열리기 전에 실행 → 화해/seed가 승인과 인터리브되는 버그-클래스(B-r5) 구조적 차단.

```python
@asynccontextmanager
async def lifespan(app):
    for fs in CONSOLE_FEATURESETS:                    # 예: ["vitals"]
        _reconcile_or_seed(fs)                        # ↓ 요청 수락 전 완료
    yield                                             # ── 이후에야 라우팅 개시 ──

def _reconcile_or_seed(fs: str):
    alias_target = deploy.active_version(fs)          # FS = 현재 활성 권위(결정 7-2), 디렉토리명 or None
    last = audit.last_active(fs)
    if last is None and alias_target is not None:
        # 콜드스타트: 콘솔 이전부터 champion 존재 → seed 1건(mn1)
        audit.append(event_type="BOOTSTRAP", featureset=fs,
                     from_version=None, to_version=alias_target,
                     actor_unverified="system", reason="cold-start seed")   # actor=system(결정 1 mn1)
    elif alias_target is not None and last.to_version != alias_target:
        # ②후 ③전 크래시 흔적 또는 콘솔 밖 수동 변경: 감사를 실제 alias로 끌어올림
        # 비교 양변 모두 디렉토리명이라 정상 승인 후 거짓 RECONCILE 없음(B1)
        audit.append(event_type="RECONCILE", featureset=fs,
                     from_version=last.to_version,     # prev = 감사상 직전 최종 활성(archived 도출 보존, mn-r5)
                     to_version=alias_target,          # target = 실제 alias(디렉토리명)
                     actor_unverified="system", reason="bootstrap reconcile")
    elif alias_target is None and last is not None:
        # 심링크 소실 + 감사 이력 존재(mn3): 없는 champion을 감사로 날조하지 않는다(거짓 복원 금지).
        _alert_missing_alias(fs)                       # 경보만, 감사 append 없음(결정 7-2 권위 원칙)
```

- **alias = 현재 활성 권위, DB = 이력 (결정 7-2)**: 분기 시 alias가 이긴다. 화해는 감사를 alias 상태로 끌어올리되 `from_version`엔 감사상 직전 최종 활성을 채워 archived 도출(이전 활성→비활성 천이)을 보존한다.
- **콜드스타트 (mn1)**: 감사 이력이 비었는데 champion이 있으면 거짓 복원 없이 seed 1건만. 콘솔 이전 과거 이력은 비어 있음을 UI에 명시(핸드오프 B).

### (b) 전파 확인 폴링 (결정 2-A MJ-new1)

alias·감사는 이미 권위로 갱신됐고, 서빙 메모리 반영은 별개 홉이라 실패할 수 있다. **숨기지 말고 가시 상태로 노출**.

```python
def _propagate_and_confirm(fs: str, *, timeout_s=10, interval_s=0.5) -> str:
    _trigger_reload(fs)                               # dev: POST /admin/reload | prod: K8s 롤링 재시작
    target = deploy.active_version(fs)                # ★ 타겟 = 현재 alias(그 swap의 버전 아님, MJ-r5)
    target_run_id = _read_meta(_version_dir(target)).get("run_id")   # 접두사 재부착 금지(B2)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _get_health(fs).get("run_id") == target_run_id:    # /health.run_id == 현재 alias의 run_id
            return "confirmed"
        time.sleep(interval_s)
    return "pending"                                  # 실패 시 재시도·경보(채널은 명세부), UI는 "전파 대기/실패"
```

- **타겟 = 현재 alias (MJ-r5)**: 연속 승인 A(→V2)·B(→V3)가 순차 통과하면 alias·서빙은 V3로 수렴. 타겟을 "그 swap의 버전(V2)"으로 두면 A 폴링이 정당히 대체된 V2를 영원히 실패 표시. 현재 alias로 정의하면 둘 다 V3 수렴 시 confirmed.
- **`/health.run_id` 비교 (MJ2)**: alias명(`gru_vitals`) 문자열이 아니라 **현재 alias 타겟 dir의 `meta.json.run_id`**와 비교. console-prep가 `/health`에 실제 run_id를 보고하게 했으므로 성립.
- **확인 전 UI 표시**: confirmed 전까지 "활성(전파 확인됨)"이 아니라 "전파 대기/실패"로 구분 표시(핸드오프 B 계약).

### 구현 2 보강: `versions` 계약 명문화 (spec-writer 모호성 + R2 minor 2건 해소)

성공기준 3의 서비스 함수명·응답 JSON이 미명문이라 구현자가 임의 결정할 여지가 있었다(명세 자립성 빈틈). 아래로 확정 — 디스크 파일 필드를 그대로 노출, 새 모델링 없음.

**함수명·시그니처** [우리 결정]:
```python
# service.py
def list_versions(fs: str) -> dict: ...                 # GET /console/versions?fs= 페이로드
def get_version_detail(fs: str, version: str) -> dict: ...  # GET /console/versions/{version}?fs=
```

**`list_versions(fs)` 응답 (리스트 — 행 렌더용 경량)**:
```json
{
  "featureset": "vitals",
  "active": "1719XXXX-B",               // = active_version(fs)에서 'gru_vitals@' 떼낸 버전. FS 권위
  "versions": [
    {
      "version": "1719XXXX-B",          // dir명 'gru_<fs>@' 접두 제거(B2: 이중접두 금지)
      "bucket": "champion",             // champion|challenger|incomplete|archived
      "ready": true,                    // .ready 존재
      "gate_passed": true,              // validation.json.no_regression. incomplete면 null
      "bholdout_util": 0.2488,          // 행 헤드라인 1수치(validation.json). 없으면 null
      "has_mlflow": true                // meta.json.run_id 존재 여부(링크 가능?)
    }
  ]
}
```
- **버킷 판정 (상호배타 — 우선순위 순으로 첫 매치)**: 한 버전은 정확히 한 버킷. 아래 순서로 판정하고 첫 매치에서 멈춘다(`champion > archived > challenger > incomplete`):
  1. `champion` = `active_version(fs)` 타겟(= 현재 alias, FS 권위; 감사 DB 추정 아님).
  2. `archived` = 현재 비활성(=champion 아님)이고 감사 `last_active` 이력상 **과거에 활성이었던** 적이 있다. **`.ready` 유무와 무관** — 과거 활성 이력이 challenger 조건(`.ready`+비활성)보다 우선하므로, 한때 챔피언이었던 버전은 `.ready`가 남아 있어도 `archived`다(되돌아갈 수 있는 롤백 후보로 표시).
  3. `challenger` = `.ready` 있고 비활성이며 **과거 활성 이력 없음**(= 한 번도 배포된 적 없는 신규 후보).
  4. `incomplete` = `.ready` 없음(영속 미완성).
  - 이로써 옛 정의의 "비challenger" 순환을 제거: archived가 challenger보다 먼저 판정되므로 "ready한 과거 챔피언"은 archived로 확정된다.

**`get_version_detail(fs, version)` 응답 (상세 — 펼친 패널용)**:
```json
{
  "version": "1719XXXX-B",
  "bucket": "challenger",
  "ready": true,
  "gate": { /* validation.json 통째 */
    "no_regression": true, "bholdout_util": 0.2488, "bholdout_prauc": 0.31,
    "new_aval_util": 0.4023, "old_aval_util": 0.4087,
    "new_aval_prauc": 0.44, "old_aval_prauc": 0.45,
    "eps": 0.02, "cross_site_claim": false, "validated_at": "2026-..Z" },
  "retrain": { /* retrain.json 통째 */
    "epochs": 6, "val_loss": 0.12, "b_split_seed": 42,
    "n_train_pids": 30269, "n_b_retrain": 14000, "n_b_holdout": 6000,
    "run_id": "a1b2c3", "git_commit": "9f8e7d" },
  "meta": { "featureset": "vitals", "tau": 0.41, "trained_on": "A-train+B-retrain" },
  "mlflow_link": "<MLFLOW_UI_BASE>/#/experiments/.../runs/a1b2c3"  // run_id 없으면 null(6-A 폴백)
}
```
- 상세는 디스크 3파일을 *그대로* 읽어 반환(가공 없음). `gate`·`retrain`은 각 JSON 통짜라 스키마 진화에 강함. `incomplete` 버전은 상세 요청 시 404(또는 `gate`/`retrain` null) — `.ready` 없으면 두 파일 부재 가능.

**헬퍼·상수 위치 (R2 minor 2건)** [우리 결정]:
- `_read_meta`·`_read_validation`·`_read_retrain`은 **콘솔 신규 헬퍼**(`console/service.py` 내부, version dir에서 JSON 로드). 서빙 `bundle.py`의 로더와 별개 — 콘솔은 모델 가중치를 로드하지 않고 메타 JSON만 읽는다.
- `CONSOLE_FEATURESETS`는 `console/service.py` 모듈 상수(기본 `["vitals"]`, 환경변수 `CONSOLE_FEATURESETS`로 override). lifespan 화해·버전 스캔의 featureset 목록 단일 출처.
- `MLFLOW_UI_BASE`(deep-link 호스트)는 **sqlite tracking path와 별개** — MLflow UI 서버 URL이라 배포환경 종속 `[검증 필요]`. 환경변수로 주입, 미설정 시 `mlflow_link=null`(폴백 6-A).

---

## 성공 기준 (spec-writer TDD 대상)

테스트 위치: `tests/console/`(신규). conftest는 임시 ARTIFACTS·인메모리/임시 sqlite로 격리(console-prep 관습 따름).

1. **감사 append-only**: `audit.append`로 INSERT는 되나, 기존 레코드 UPDATE/DELETE 시도는 `PermissionError`. `ts`는 UTC.
2. **감사 스키마**: APPROVE 레코드에 `gate_snapshot`이 version dir `validation.json`의 사본으로 박히고, `gate_passed`가 `no_regression`과 일치. `actor_unverified` 기본값 존재, `verified_subject`는 NULL.
3. **versions 분류**: `.ready` 있는 비활성 = challenger, 없으면 incomplete, `active_version` 타겟 = champion. champion 판정이 alias(FS)에서 나오고 감사 DB 추정이 아님.
4. **approve 경로**: `.ready` 없는 version 승인 시 422(미완성 거부). `validation.json` → SimpleNamespace 복원으로 `deploy.swap` 호출이 `getattr(no_regression)` 정합. REGRESSED 버전은 422(`ValueError` 변환). 성공 시 prev·active 반환 + 감사 1건.
5. **rollback 경로**: validation 재검증 없이 실행되나 **승인+감사는 필수**(감사 1건, `event_type=ROLLBACK`, `gate_passed=NULL`). 롤백 prev = 사전 `active_version` 캡처(mn-c).
6. **직렬화(면 2)**: 같은 featureset 동시 approve 2건이 직렬화돼 둘째가 갱신된 active를 prev로 읽음(prev 갈라짐 없음). 감사에 `V1→V2`·`V2→V3`로 남고 `V1→V3` 오염 없음.
7. **화해(면 1)**: alias가 감사 `last_active`와 다른 상태로 부팅 시 `RECONCILE` 레코드 1건이 `from=감사최종·to=실제alias`로 기록되고, archived 도출이 보존됨. 콜드스타트(감사 빔+champion 존재)는 `BOOTSTRAP` seed 1건. **화해/seed가 라우팅 개시 전 완료**(lifespan 순서).
8. **전파 확인**: `_propagate_and_confirm`이 `/health.run_id == 현재 alias run_id`면 confirmed, 타임아웃이면 pending. 타겟이 "그 swap 버전"이 아니라 "현재 alias"임(연속 승인 시 옛 버전 거짓 실패 없음, MJ-r5).
9. **누수 불변**: 콘솔 계층이 환자 단위 B 분할·train-only stats·0-fill 금지·mask OFF를 건드리지 않음(노출·기록 계층 한정).

## 범위 외 (명시)

- React 통합 콘솔·Grafana 패널 → **핸드오프 B**.
- 다중 프로세스/replica 직렬화(공유 저장소 락) → 콘솔 1프로세스 전제. scale-out 시 DB advisory lock/파일 lock으로 승격(결정 7-1 의존 식별). 본 핸드오프는 프로세스-로컬 락.
- 정확한 K8s RBAC·롤링 재시작 매니페스트 → 배포환경 종속 `[검증 필요]`. dev는 `/admin/reload` 경로로 테스트.
- 인증/SSO·`verified_subject` 채움 → 후속 과제(M4). MVP는 `actor_unverified`.
- `deploy.rollback`의 `approved` 가드 대칭화 → H4r 코드 변경, 권고만(5-A). 콘솔은 사전 active 읽기로 우회.