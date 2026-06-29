# Console 백엔드 핸드오프 A (명세부) 레드팀 검토

> 대상: `design/console/handoff_backend.md` · 설계 권위: `design/console/decisions.md`(결정 4·5·5-A·5-B·6-A·7)
> 루프: redteam ⇄ reviser. 통과 = blocker 0. 각 지적 아래 `[reviser 응답]`으로 해소/미해소를 대조 기록.

---

## 라운드 1 (2026-06-30, redteam 원문)

- 대상: design/console/handoff_backend.md (신규, 첫 검토)
- 핵심 질문: 결정 4·5·5-A·5-B·6-A·7을 누락·왜곡 없이 구현 명세하는가. 승인·롤백·화해·전파의 의존 사슬이 코드 수준에서 끝까지 닫히는가.
- 판정: HOLD — blocker 2건 (B1 버전 식별자 규약, B2 전파 확인 경로). major 2건, minor 3건.

근본 원인 한 줄: deploy.active_version()은 *버전 디렉토리명*(gru_vitals@v3)을 반환하는데, 핸드오프의 approve/rollback은 *맨버전*(v3)을 감사·경로에 쓴다. 이 규약 불일치가 화해 비교·전파 경로·archived 사슬을 동시에 깬다. 설계부 review(라운드 1~6)는 decisions.md만 봤기에 이 코드-수준 결함을 잡을 수 없었다.

### PASS
- 임계구간 구조가 결정 7-1과 정합 — approve/rollback 모두 with _LOCKS[fs]: 안에서 read-active(prev) → swap/rollback → audit.append 순서, propagation은 구간 밖(handoff:107-116, 121-128). featureset 단위 프로세스-로컬 락이 결정 7-1·2의 "1프로세스 전제, scale-out 시 공유락 승격"과 일치.
- 부팅 화해 선완료가 결정 7-1 경계 완전성과 정합 — lifespan이 _reconcile_or_seed(fs)를 yield(라우팅 개시) 전에 실행(handoff:147-151). reconcile↔승인 인터리브가 구조적으로 불가.
- swap 복원 경로가 deploy.swap 계약과 정합 (면 4) — _restore_validation이 validation.json → SimpleNamespace, deploy.swap은 getattr(validation,"no_regression",False)로 속성 접근(deploy.py:86). 객체 래핑이 dict-아님 요구를 충족. validation.json에 validated_at 추가분은 swap이 안 읽으므로 무해.
- 게이트 스냅샷 출처 = 디스크 validation.json (N1) — _read_gate_snapshot이 version dir의 validation.json 사본을 박고 gate_passed=bool(snap.get("no_regression"))(handoff:110-114). materialize가 validation.json을 .ready 전에 원자 기록하므로 빈 스냅샷 불가.
- /health.run_id 비교 사슬 실재 (MJ2) — /health가 s["bundle"].run_id 보고(app.py:109), load_bundle_from_dir가 meta.get("run_id", d.name)(bundle.py:102), materialize가 meta.json에 run_id 기록(deploy.py:52).
- 신규 파일 경로 충돌 없음 (면 5) — src/sepsis/console/는 현재 미존재. serve/retrain과 책임 분리 합당(api=HTTP, service=로직, audit=ORM).
- swap keyword-only 시그니처 정확 — handoff:12·109가 validation=·approved= 키워드 호출, deploy.py:80과 일치.
- 누수 4종 무영향 — 콘솔은 노출·기록 계층, 재학습 파이프라인 불변(성공기준 9).

### blocker

#### B1. 버전 식별자 규약이 미정·모순 — 화해 비교·archived 사슬이 깨진다 (면 1·2)
- 문제: deploy.active_version(fs)는 os.readlink로 디렉토리명 전체(gru_vitals@v3)를 반환한다(deploy.py:71-73, 핸드오프 line 11도 "버전 디렉토리명 반환"으로 인정). 그런데 approve/rollback은 맨버전을 감사에 쓴다:
  - approve(fs, version, ...): version_dir = ARTIFACTS / f"gru_{fs}@{version}" 이므로 version은 맨버전(v3). 그대로 to_version=version(handoff:113).
  - 같은 레코드의 from_version=prev = deploy.active_version(fs) = 디렉토리명(gru_vitals@v2)(handoff:108,113). 한 레코드 안에서 from=디렉토리명, to=맨버전으로 혼재.
- 근거 / 깨지는 흐름:
  1. 화해 거짓양성 (면 2 핵심): _reconcile_or_seed에서 last.to_version != alias_target 비교(handoff:160). 정상 승인 1회 후 재기동 시 last.to_version="v3" vs alias_target=active_version()="gru_vitals@v3" → 항상 불일치 → 매 부팅마다 거짓 RECONCILE 기록. 결정 7-2가 "②후③전 크래시 흔적"에만 reconcile하도록 못 박은 의미가 무너짐.
  2. archived 사슬 단절: 연속 승인 record1.to=v3, record2.from=gru_vitals@v3 → 링크 불일치로 "이전 활성→비활성" 천이 도출(결정 1 archived)이 오염. 성공기준 6의 "V1→V2·V2→V3로 남음"이 코드상 성립 불가.
  3. 롤백 타겟 사용불가: deploy.rollback(fs, previous_version_name) → set_alias(..., previous_version_name)는 그 문자열을 상대 심링크 타겟으로 그대로 씀(bundle.py:35,95). 감사에서 읽은 맨버전 v2를 넘기면 심링크가 존재하지 않는 v2를 가리켜 깨짐 — 유효 타겟은 디렉토리명 gru_vitals@v2여야 함.
- 제안: 감사 from_version/to_version/approve(version)/rollback(target_version)/reconcile 비교가 쓰는 식별자를 하나로 못 박아라(권장: active_version이 반환하는 디렉토리명으로 통일, 또는 전 경로에서 맨버전으로 정규화하는 헬퍼를 명세). 어느 쪽이든 _reconcile_or_seed의 비교·롤백 타겟·archived 링크가 같은 표현을 쓰도록 명세부에 고정.

> **[reviser 응답]** 해소: 신규 절 **"버전 식별자 규약"**(handoff_backend.md:20-49)을 추가해 정규 식별자 = **버전 디렉토리명** `gru_<fs>@<v>`로 단일화. 맨버전 단독 등장 금지를 명문화하고, 경로 구성을 `_version_dir(version_id) = ARTIFACTS / version_id`(접두사 재부착 금지) 헬퍼로 일원화. (1) approve 시그니처를 `approve(fs, version_id, ...)`로 바꾸고 `version_dir = _version_dir(version_id)`, `to_version=version_id`로 정정(approve 본문). (2) rollback도 `rollback(fs, target_version_id, ...)` + `deploy.rollback(fs, target_version_id)`(디렉토리명을 심링크 타겟으로) + `to_version=target_version_id`. (3) `_reconcile_or_seed`는 `last.to_version`·`alias_target` 둘 다 이제 디렉토리명이라 비교가 정합(거짓 RECONCILE 제거). (4) `_require_consistent(fs, version_id)`로 교차-fs 오승인 가드 추가. 화해 비교·롤백 타겟·archived 링크·전파 타겟이 모두 동일 표현(디렉토리명)을 쓴다.

#### B2. 전파 확인 경로가 이중 접두사 — 항상 "pending" (면 3, MJ-r5 구현 결함)
- 문제: _propagate_and_confirm에서
    target = deploy.active_version(fs)                      # = "gru_vitals@v3" (디렉토리명)
    target_run_id = _read_meta(ARTIFACTS / f"gru_{fs}@{target}").get("run_id")
  (handoff:178-179). target이 이미 디렉토리명인데 다시 gru_{fs}@를 붙여 ARTIFACTS / "gru_vitals@gru_vitals@v3" — 존재하지 않는 경로. _read_meta가 실패/빈 dict → target_run_id=None → /health.run_id(실제 run_id)와 영원히 불일치 → 모든 승인·롤백이 항상 "pending" 반환.
- 근거: 핸드오프 line 11 자신이 active_version을 "디렉토리명 반환"으로 적었는데, line 179에서 맨버전인 양 접두사를 다시 붙임 — 문서 내부 모순. MJ-r5의 "타겟=현재 alias" 의도는 옳으나(line 178), 경로 구성이 그 의도를 실행 불가로 만듦.
- 제안: ARTIFACTS / target(접두사 없이) 또는 deploy.active_reference_path류 헬퍼로 디렉토리명을 직접 경로화. B1의 규약 통일과 함께 정정.

> **[reviser 응답]** 해소: `_propagate_and_confirm`에서 `_read_meta(ARTIFACTS / f"gru_{fs}@{target}")` → `_read_meta(_version_dir(target))`로 정정(접두사 재부착 제거). `target = deploy.active_version(fs)`는 디렉토리명이므로 `_version_dir(target) = ARTIFACTS / target`이 올바른 경로. B1의 규약 절이 "접두사 재조립 금지"를 전 구간에 못 박아 이 버그-클래스를 구조적으로 차단. 문서 내부 모순(line 11 ↔ 옛 179) 제거됨.

### major

#### MJ1. 감사 git_commit이 항상 NULL — 잘못된 출처 파일에서 읽음
- 문제: approve가 meta = _read_meta(version_dir) 후 git_commit=meta.get("git_commit")(handoff:112-113). 그러나 materialize의 meta.json엔 git_commit이 없다(deploy.py:49-52: featureset·hp·input_dim·tau·version·trained_on·run_id뿐). git_commit은 retrain.json에 있다(deploy.py:64). → 감사 git_commit이 데이터가 실재함에도 항상 NULL. 결정 4 "MLflow 링크 키 박제"의 git_commit이 유실.
- 제안: git_commit은 retrain.json에서 읽도록 명세(예: _read_retrain(version_dir).get("git_commit")). 또는 materialize가 meta.json에도 git_commit 기록(콘솔 밖 변경).

> **[reviser 응답]** 해소: approve가 `retr = _read_retrain(version_dir)`를 읽고 `git_commit=retr.get("git_commit")`로 정정(meta.json이 아니라 retrain.json 출처). `_read_retrain` 헬퍼를 코드 현황·헬퍼 목록에 추가. 스키마 주석(`git_commit` 컬럼)도 "출처 = retrain.json(deploy.py:64), meta.json엔 없음"으로 명시. run_id는 meta.json에 실재하므로(deploy.py:52) 그대로 meta 출처 유지.

#### MJ2. append-only 강제가 bulk UPDATE/DELETE를 못 막음
- 문제: before_flush 훅이 session.dirty/session.deleted만 검사(handoff:57-60). SQLAlchemy의 Query.update()/Query.delete()·Session.execute(update(...))는 unit-of-work를 우회해 before_flush를 타지 않는다. 즉 명세된 유일한 불변성 강제 수단에 우회 구멍. 성공기준 1은 ORM 경로 UPDATE/DELETE만 테스트해 이 구멍을 안 봄.
- 제안: bulk 경로 차단(do_orm_execute 훅에서 UPDATE/DELETE statement 거부) 또는 DB 레벨 강제(권한 회수/트리거)를 명세에 명시하고, 성공기준에 bulk 우회 테스트 추가.

> **[reviser 응답]** 해소: append-only 강제를 **이중 훅**으로 명세 — (1) `before_flush`(ORM unit-of-work UPDATE/DELETE 차단, 기존), (2) `do_orm_execute`(bulk `update()`/`delete()`·`Session.execute(update/delete(...))` 차단: `state.is_update or state.is_delete`면 거부). bulk 경로가 before_flush를 우회하는 구멍을 do_orm_execute가 막는다고 명시. 성공기준 1에 **bulk 우회 테스트**(`session.execute(update(AuditEvent)...)`·`delete(...)`가 거부됨) 추가. DB 레벨 강제(트리거/권한 회수)는 PostgreSQL 승격 시 [검증 필요]로 권고.

### minor
- mn1. BOOTSTRAP actor가 결정 1과 불일치 — 부트스트랩 seed가 actor_unverified를 안 넘겨(handoff:158-159) 스키마 기본값 "operator"로 기록됨. 결정 1 mn1은 action=bootstrap, actor=system 명시. actor_unverified="system" 명시 권고(reconcile 분기 line 165는 이미 그렇게 함).

> **[reviser 응답]** 해소: BOOTSTRAP append에 `actor_unverified="system"` 추가(handoff:158-159). 결정 1 mn1(actor=system)과 정합.

- mn2. CONSOLE_FEATURESETS 출처 미정의 — lifespan(handoff:149)이 참조하나 정의·기본값 출처 미명세. config 상수 위치 고정 권고.

> **[reviser 응답]** 해소: `CONSOLE_FEATURESETS`를 `console/__init__.py`(또는 `console/config.py`) 모듈 상수로 고정하고 기본값 `["vitals"]`, 환경변수 override 가능으로 명세. lifespan 주석에 출처 명시.

- mn3. alias=None인데 감사 이력 존재 케이스 미처리 — _reconcile_or_seed의 분기(handoff:156,160)가 alias_target is None이면서 last is not None인 경우(심링크 소실+이력 존재)를 처리 안 함. 한 줄 동작 명시 권고.

> **[reviser 응답]** 해소: `_reconcile_or_seed`에 `elif alias_target is None and last is not None:` 분기 추가 — alias(현재 활성 권위)가 None이면 champion 부재이므로 **거짓 감사 레코드를 만들지 않고** 운영자 경보 + UI "활성 없음(alias 소실)" 표시만 한다(alias 권위 원칙: 없는 champion을 감사로 날조 금지). 결정 7-2와 정합.

### 종합
blocker 2건(B1·B2)은 동일 근본 원인(맨버전 vs 디렉토리명 규약 미정)에서 갈라지며, 화해·전파·archived·롤백 네 흐름을 코드 수준에서 끊는다. 둘 다 핸드오프 내부 모순(line 11이 적은 사실과 line 113·179가 충돌)이라 spec-writer/구현자가 글자대로 따르면 동작하지 않는다. HOLD — 다음 단계(spec-writer TDD) 진입 불가.

> **[reviser 종합 응답]** B1·B2를 "버전 식별자 규약 = 디렉토리명 단일화" 한 절로 함께 닫고(헬퍼 `_version_dir`/`_require_consistent`), MJ1(git_commit→retrain.json)·MJ2(do_orm_execute bulk 차단 + 성공기준 테스트)·minor 3건 모두 반영. 설계부 결정 4·5·7 범위 내 구현 명세만 추가했고 새 설계 결정은 만들지 않음. 코드 대조: active_version=디렉토리명 반환(deploy.py:71-73), set_alias가 그 문자열을 심링크 타겟으로 사용(bundle.py:35), git_commit은 retrain.json만 보유(deploy.py:64) — 모두 정합 확인.
