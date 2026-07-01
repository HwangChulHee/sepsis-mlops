"""성공기준 3 — versions 분류 (handoff_backend.md v2 "구현 2 보강: versions 계약" 절, :192-246).

이전엔 함수명·응답 스키마가 미명문이라 엔드포인트-우회 + skip/[검증 필요] 가정으로 느슨히
검증했다. handoff v2 가 함수명·JSON 스키마를 **계약으로 박았으므로** 이를 직접 호출·명시
단언으로 승격한다.

확정 계약(handoff v2 — 직접 읽고 따름):
- service.py 두 함수:
    list_versions(fs: str) -> dict
    get_version_detail(fs: str, version: str) -> dict
- list_versions(fs) 응답(리스트, handoff:203-220):
    최상위 {featureset, active, versions:[...]}.
    active = active_version(fs) 에서 'gru_<fs>@' 접두 제거한 버전(FS 권위).
    각 행 {version, bucket, ready, gate_passed, bholdout_util, has_mlflow}.
      version       = dir명 'gru_<fs>@' 접두 제거(B2: 이중접두 금지).
      bucket        ∈ {champion, challenger, incomplete, archived}.
                      champion = active_version(fs) 타겟(alias 권위, 감사 DB 추정 아님).
                      challenger = .ready 있고 비활성. incomplete = .ready 없음.
      gate_passed   = validation.json.no_regression (incomplete면 null).
      bholdout_util = validation.json 헤드라인 (없으면 null).
      has_mlflow    = meta.json.run_id 존재 여부.
- get_version_detail(fs, version) 응답(상세, handoff:222-241):
    {version, bucket, ready, gate, retrain, meta, mlflow_link}.
      gate        = validation.json 통째.
      retrain     = retrain.json 통째.
      meta        = meta.json 일부(featureset·tau·trained_on).
      mlflow_link = run_id 로 만든 URL. **run_id 없으면 null (6-A 폴백, handoff:238)**.

[검증 필요] 선행:
- service.list_versions / service.get_version_detail 모듈 전역 함수 실재(handoff:199-201 명문).
- mlflow_link 생성엔 MLFLOW_UI_BASE 주입 필요(미설정 시 폴백 null, handoff:246).

구현(src/sepsis/console/)이 없으니 지금은 RED(console fixture 의 ModuleNotFoundError)가 정상.
src/ 구현 코드는 읽지 않았다 — 핸드오프 v2 가 명문화한 함수명·JSON 키만 신뢰해 단언한다.
"""
from __future__ import annotations

import json


def _rows_by_version(payload):
    """list_versions 응답의 versions 리스트 → {version: row} 매핑."""
    assert "versions" in payload, f"list_versions 응답에 'versions' 키 없음: {payload}"
    assert isinstance(payload["versions"], list), \
        f"versions 는 리스트여야: {type(payload['versions'])}"
    return {row["version"]: row for row in payload["versions"]}


# ===== 1. 함수명·시그니처 실재 + list_versions 최상위 키 계약 (성공기준 3, handoff:199-218) =====
def test_list_versions_toplevel_contract(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)   # dir = gru_vitals@champ
    console.fd.set_active(fs, champ_id)             # alias = champion

    # 함수명 실재 — 이전엔 엔드포인트로만 우회했으나 이제 직접 호출
    payload = console.service.list_versions(fs)
    assert isinstance(payload, dict)
    # 최상위 키 계약(handoff:205-208)
    assert payload["featureset"] == fs, "최상위 featureset 누락/불일치"
    assert "active" in payload, "최상위 active 누락"
    assert "versions" in payload, "최상위 versions 누락"
    assert isinstance(payload["versions"], list)
    # active = alias 타겟에서 접두 제거(FS 권위, B2)
    assert payload["active"] == "champ", f"active 가 접두 제거된 alias 버전이 아님: {payload['active']}"
    assert not payload["active"].startswith("gru_"), "active 에 'gru_' 접두 잔존(B2 위반)"


# ===== 2. champion=alias / challenger=ready-비활성 / incomplete=non-ready (성공기준 3, handoff:220) =====
def test_versions_classify_three_buckets(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)
    chal_id, _ = console.mk("chal", ready=True)
    inc_id, _ = console.mk("inc", ready=False)      # .ready 없음 = 미완성
    console.fd.set_active(fs, champ_id)             # alias = champion

    rows = _rows_by_version(console.service.list_versions(fs))
    assert rows["champ"]["bucket"] == "champion", f"alias 타겟이 champion 아님: {rows['champ']}"
    assert rows["chal"]["bucket"] == "challenger", f"ready-비활성이 challenger 아님: {rows['chal']}"
    assert rows["inc"]["bucket"] == "incomplete", f".ready 없는 버전이 incomplete 아님: {rows['inc']}"
    # 미완성은 challenger 로 새지 않는다(.ready 게이트)
    assert rows["inc"]["bucket"] != "challenger"


# ===== 3. 각 version 행의 키·의미 계약 (성공기준 3, handoff:208-216) =====
def test_version_row_keys(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True, run_id="abc123")  # no_regression=True 기본
    console.fd.set_active(fs, champ_id)

    rows = _rows_by_version(console.service.list_versions(fs))
    row = rows["champ"]
    for k in ("version", "bucket", "ready", "gate_passed", "bholdout_util", "has_mlflow"):
        assert k in row, f"version 행 키 누락: {k} not in {row}"
    assert row["ready"] is True, ".ready 있는 dir 인데 ready!=True"
    assert row["gate_passed"] is True, "validation.json.no_regression=True 인데 gate_passed!=True"
    assert row["bholdout_util"] == 0.42, "bholdout_util 가 validation.json 헤드라인과 불일치"
    assert row["has_mlflow"] is True, "meta.json.run_id 있는데 has_mlflow!=True"


# ===== 3b. incomplete 면 gate_passed=null (handoff:213 — 별도 단언) =====
def test_incomplete_gate_passed_null(console):
    fs = "vitals"
    inc_id, _ = console.mk("inc", ready=False)      # .ready 없음 → incomplete

    rows = _rows_by_version(console.service.list_versions(fs))
    row = rows["inc"]
    assert row["bucket"] == "incomplete"
    # 계약: incomplete 버킷이면 validation.json 유무와 무관하게 gate_passed=null
    assert row["gate_passed"] is None, "incomplete 면 gate_passed=null 이어야(handoff:213)"


# ===== 3c. has_mlflow = meta.json.run_id 존재 여부 (handoff:216) =====
def test_has_mlflow_false_when_no_run_id(console):
    fs = "vitals"
    champ_id, d = console.mk("champ", ready=True)
    # make_version_dir 는 run_id 를 항상 채우므로 meta.json 에서 직접 제거
    meta = json.loads((d / "meta.json").read_text())
    meta.pop("run_id", None)
    (d / "meta.json").write_text(json.dumps(meta))
    console.fd.set_active(fs, champ_id)

    rows = _rows_by_version(console.service.list_versions(fs))
    assert rows["champ"]["has_mlflow"] is False, "meta.json.run_id 없으면 has_mlflow=False(handoff:216)"


# ===== 2b. champion 은 alias 권위 — 감사 DB 추정이 아님 (결정 7-2, handoff:220) =====
def test_champion_from_alias_not_audit_db(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)
    other_id, _ = console.mk("other", ready=True)
    # 감사상 최종 활성을 other 로 심어 둠(champion 을 감사로 추정하면 other 가 champion 으로 나올 것)
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version=None, to_version="other", gate_passed=True)
    console.fd.set_active(fs, champ_id)             # 그러나 실제 alias = champ

    rows = _rows_by_version(console.service.list_versions(fs))
    assert rows["champ"]["bucket"] == "champion", "champion 이 alias(champ)가 아님"
    assert rows["other"]["bucket"] != "champion", \
        "champion 을 감사 DB 로 추정함(alias 권위 위반 — 결정 7-2)"
    # 최상위 active 도 alias 를 따른다
    assert console.service.list_versions(fs)["active"] == "champ"


# ===== 4. version 접두 제거 — 응답 표면은 맨버전, 내부 식별자(dir명)와 구분 (B2, handoff:210) =====
def test_version_prefix_stripped_in_response_b2(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)   # 내부 dir = gru_vitals@champ
    chal_id, _ = console.mk("chal", ready=True)
    console.fd.set_active(fs, champ_id)

    payload = console.service.list_versions(fs)
    # 최상위 active 접두 제거
    assert payload["active"] == "champ"
    # 모든 행 version 이 접두/구분자 없는 맨버전 표현
    for row in payload["versions"]:
        assert not row["version"].startswith("gru_"), f"행 version 에 'gru_' 접두 잔존: {row['version']}"
        assert "@" not in row["version"], f"행 version 에 dir 구분자 '@' 잔존: {row['version']}"
    # 상세 표면도 동일하게 맨버전
    detail = console.service.get_version_detail(fs, "champ")
    assert detail["version"] == "champ", f"상세 version 이 맨버전이 아님: {detail['version']}"


# ===== 5. get_version_detail 함수 실재 + 키 계약 (성공기준 3, handoff:222-241) =====
def test_get_version_detail_keys(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)
    chal_id, _ = console.mk("chal", ready=True)
    console.fd.set_active(fs, champ_id)             # champ=champion → chal=challenger

    detail = console.service.get_version_detail(fs, "chal")
    assert isinstance(detail, dict)
    for k in ("version", "bucket", "ready", "gate", "retrain", "meta", "mlflow_link"):
        assert k in detail, f"상세 응답 키 누락: {k} not in {list(detail)}"
    assert detail["version"] == "chal"
    assert detail["bucket"] == "challenger", "ready-비활성 버전 상세의 bucket 이 challenger 아님"
    assert detail["ready"] is True


# ===== 5b. gate=validation.json 통째 · retrain=retrain.json 통째 · meta 일부 (handoff:228-237) =====
def test_detail_gate_and_retrain_are_whole_json(console):
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True, git_commit="cafef00d")
    console.fd.set_active(fs, champ_id)

    detail = console.service.get_version_detail(fs, "champ")

    # gate = validation.json 통째 — ValidationResult 전 필드 보존(스키마 진화 강건)
    gate = detail["gate"]
    for k in ("no_regression", "bholdout_util", "bholdout_prauc",
              "new_aval_util", "old_aval_util", "new_aval_prauc",
              "old_aval_prauc", "eps", "cross_site_claim"):
        assert k in gate, f"gate(validation.json 통째)에 {k} 누락"
    assert gate["no_regression"] is True
    assert gate["bholdout_util"] == 0.42

    # retrain = retrain.json 통째
    retrain = detail["retrain"]
    for k in ("epochs", "val_loss", "b_split_seed", "n_train_pids",
              "n_b_retrain", "n_b_holdout", "run_id", "git_commit"):
        assert k in retrain, f"retrain(retrain.json 통째)에 {k} 누락"
    assert retrain["git_commit"] == "cafef00d"

    # meta = meta.json 일부(featureset·tau·trained_on)
    meta = detail["meta"]
    assert meta["featureset"] == fs
    assert meta["tau"] == 0.5
    assert "trained_on" in meta


# ===== 5c. mlflow_link — run_id 있고 MLFLOW_UI_BASE 주입 시 링크 생성 (6-A, handoff:238/246) =====
def test_detail_mlflow_link_present_when_run_id(console):
    fs = "vitals"
    # [검증 필요] 선행: MLFLOW_UI_BASE 주입돼야 링크 생성(미설정 시 폴백 null, handoff:246)
    console.monkeypatch.setenv("MLFLOW_UI_BASE", "http://mlflow.example")
    console.monkeypatch.setattr(console.service, "MLFLOW_UI_BASE",
                                "http://mlflow.example", raising=False)
    champ_id, _ = console.mk("champ", ready=True, run_id="a1b2c3")
    console.fd.set_active(fs, champ_id)

    detail = console.service.get_version_detail(fs, "champ")
    assert detail["mlflow_link"] is not None, "run_id+MLFLOW_UI_BASE 있는데 mlflow_link=null"
    assert "a1b2c3" in detail["mlflow_link"], "mlflow_link 가 run_id 로 구성돼야(6-A, handoff:238)"


# ===== 5d. mlflow_link — run_id 없으면 null (6-A 폴백, handoff:238 — 별도 케이스) =====
def test_detail_mlflow_link_null_when_no_run_id(console):
    fs = "vitals"
    # MLFLOW_UI_BASE 가 있어도 run_id 부재면 죽은 링크 만들지 않고 null
    console.monkeypatch.setenv("MLFLOW_UI_BASE", "http://mlflow.example")
    console.monkeypatch.setattr(console.service, "MLFLOW_UI_BASE",
                                "http://mlflow.example", raising=False)
    champ_id, d = console.mk("champ", ready=True)
    meta = json.loads((d / "meta.json").read_text())
    meta.pop("run_id", None)
    (d / "meta.json").write_text(json.dumps(meta))
    console.fd.set_active(fs, champ_id)

    detail = console.service.get_version_detail(fs, "champ")
    assert detail["mlflow_link"] is None, "run_id 없으면 mlflow_link=null 이어야(6-A 폴백, handoff:238)"


# =====================================================================================
# ===== archived 버킷 보강 (handoff "버킷 판정" 절, :262-267 — 갱신된 archived 규약) =====
# =====================================================================================
# 확정 규약(handoff:262-267, 직접 읽고 따름) — 상호배타·우선순위 첫 매치:
#     champion > archived > challenger > incomplete
#   1. champion   = active_version(fs) 타겟(현재 alias, FS 권위).
#   2. archived   = 현재 비활성이고 감사 last_active 이력상 **과거에 활성이었던** 적 있음.
#                   **.ready 유무 무관** — 한때 챔피언이던 버전은 .ready 가 남아 있어도 archived.
#   3. challenger = .ready 있고 비활성이며 **과거 활성 이력 없음**(신규 후보).
#   4. incomplete = .ready 없음.
#
# 감사 이력의 비교 키 = **버전 디렉토리명**으로 단일화(B1 복원, handoff:22-42/148/162).
#   - service.approve 는 to_version=version_id(=디렉토리명 'gru_<fs>@<v>', handoff:148).
#   - _reconcile_or_seed / BOOTSTRAP 도 to_version=alias_target(=deploy.active_version 반환
#     =디렉토리명, handoff:191/204). 양쪽이 같은 디렉토리명 표현이라 archived 도출 비교 키가
#   어긋나지 않는다(맨버전 단독 금지 — _require_consistent 가 'gru_<fs>@' 접두 없으면 ValueError).
#   따라서 아래 권위 테스트(approve 직접 호출)와 직접-삽입 변형 모두 **디렉토리명**으로 이력을
#   구성한다. approve 헬퍼는 console.mk 가 돌려준 version_id(=디렉토리명)를 approve 에 넘긴다.


def _approve(console, fs, label):
    """version dir 를 만들고 service.approve 로 활성화(=감사 to_version 기록).

    B1 복원: approve 의 버전 인자는 **버전 디렉토리명**(version_id='gru_<fs>@<label>')이다.
    console.mk(label) 이 (version_id, dir) 를 돌려주므로 그 version_id 를 approve 에 넘긴다
    (맨버전 라벨을 넘기면 _require_consistent 가 ValueError 로 거부, handoff:37-39).
    approve 가 swap 으로 alias 를 그 버전으로 바꾸고 APPROVE 감사 1건(to_version=version_id,
    디렉토리명)을 남긴다. 이후 다른 버전을 approve 하면 이 버전은 '비활성 + 과거 활성 이력'
    = archived 후보가 된다.
    """
    version_id, d = console.mk(label, ready=True)
    console.service.approve(fs, version_id, actor="operator")
    return version_id, d


# ===== A1. archived 분류 — 과거 활성 + 현재 비활성 → archived (박을것 #1, handoff:222) =====
def test_archived_after_supersede_via_approve_twice(console):
    """old 를 승인해 챔피언으로 올렸다가 new 로 교체 → old 는 비활성+과거활성 = archived.

    setup: service.approve 2회(권위 경로 — approve 가 직접 to_version=디렉토리명을 기록).
    [검증 필요] 선행: service.approve 가 swap+감사 append 수행(handoff:135-150),
    _propagate_and_confirm 는 fixture 에서 confirmed 스텁.
    """
    fs = "vitals"
    _approve(console, fs, "old")    # alias = gru_vitals@old (한때 챔피언)
    _approve(console, fs, "new")    # alias = gru_vitals@new → old 는 비활성

    rows = _rows_by_version(console.service.list_versions(fs))
    # new = 현재 alias = champion
    assert rows["new"]["bucket"] == "champion", f"현재 alias 가 champion 아님: {rows['new']}"
    # old = 비활성 + 감사상 과거 활성 → archived (롤백 후보)
    assert rows["old"]["bucket"] == "archived", \
        f"과거 활성·현재 비활성 버전이 archived 아님: {rows['old']}"


# ===== A2. archived 가 challenger 보다 우선 — .ready 있어도 과거활성이면 archived =====
# (박을것 #2, 핵심 회귀 방지; handoff:264/267 "ready 한 과거 챔피언은 archived 로 확정") =====
def test_archived_takes_priority_over_challenger_even_with_ready(console):
    """old: .ready=True(승인됐으니 당연) + 과거 활성 → challenger 가 아니라 archived.

    이게 옛 "비challenger 순환"을 깬 지점이다: archived 가 challenger 보다 먼저 판정되므로
    '.ready 가 남아 있는 과거 챔피언'은 archived 로 확정된다(handoff:267). 같은 스캔에
    이력 없는 신규 ready 후보(cand)를 함께 둬 priority 가 갈리는 지점을 박는다.
    """
    fs = "vitals"
    _approve(console, fs, "old")    # 과거 챔피언, 여전히 .ready 보유
    _approve(console, fs, "new")    # 현재 챔피언
    console.mk("cand", ready=True)  # 신규 후보: .ready 있고 비활성, 과거 활성 이력 없음

    rows = _rows_by_version(console.service.list_versions(fs))
    # 핵심: old 는 .ready 가 살아 있는데도 challenger 가 아니라 archived
    assert rows["old"]["ready"] is True, "과거 챔피언 old 의 .ready 가 사라짐(전제 붕괴)"
    assert rows["old"]["bucket"] == "archived", \
        f".ready 있는 과거 챔피언이 archived 로 확정되지 않음(우선순위 회귀): {rows['old']}"
    assert rows["old"]["bucket"] != "challenger", \
        ".ready+과거활성 인데 challenger 로 샘(archived>challenger 우선순위 위반 — 옛 순환 부활)"
    # 대조: 과거 활성 이력 없는 신규 ready 후보는 challenger
    assert rows["cand"]["bucket"] == "challenger", \
        f"이력 없는 신규 ready 후보가 challenger 아님: {rows['cand']}"


# ===== A3. challenger 는 과거 활성 없음 한정 (박을것 #3, handoff:223) =====
def test_challenger_requires_no_past_active_history(console):
    """challenger = .ready 있고 비활성이며 과거 활성 이력 '없음'.

    같은 조건(.ready+비활성)이라도 과거 활성 이력이 있으면 archived 로 가야 하므로,
    challenger 는 '한 번도 배포된 적 없는' 버전에만 부여돼야 한다.
    """
    fs = "vitals"
    _approve(console, fs, "old")    # 과거 활성 → archived 가 되어야(challenger 아님)
    _approve(console, fs, "new")    # 현재 챔피언
    console.mk("fresh", ready=True)  # 과거 활성 이력 전무한 신규 ready 후보

    rows = _rows_by_version(console.service.list_versions(fs))
    # 신규 후보만 challenger
    assert rows["fresh"]["bucket"] == "challenger", \
        f"과거활성 없는 ready-비활성이 challenger 아님: {rows['fresh']}"
    # 과거 활성 이력 있는 ready-비활성은 challenger 로 분류되면 안 됨
    assert rows["old"]["bucket"] != "challenger", \
        "과거 활성 이력 있는 ready-비활성이 challenger 로 샘(challenger 는 과거활성 없음 한정)"
    assert rows["old"]["bucket"] == "archived"


# ===== A4. 상호배타 — 한 버전은 정확히 한 버킷, 네 버킷 동시 공존 (박을것 #4, handoff:220) =====
def test_buckets_mutually_exclusive_all_four(console):
    """champion·archived·challenger·incomplete 가 한 스캔에 공존, 각자 정확히 한 버킷.

    우선순위 첫 매치(champion>archived>challenger>incomplete)가 각 버전을 정확히 하나로
    가른다. 버전 행이 중복되지 않고(버전당 1행), 기대 매핑과 정확히 일치해야 한다.
    """
    fs = "vitals"
    _approve(console, fs, "arch")    # 과거 챔피언 → 교체되면 archived
    _approve(console, fs, "champ")   # 현재 챔피언
    console.mk("chal", ready=True)   # ready+비활성+이력없음 → challenger
    console.mk("inc", ready=False)   # .ready 없음 → incomplete

    payload = console.service.list_versions(fs)
    versions = [row["version"] for row in payload["versions"]]
    # 버전당 정확히 1행(중복 분류 없음)
    assert len(versions) == len(set(versions)), f"버전 행 중복(상호배타 위반): {versions}"
    assert set(versions) == {"arch", "champ", "chal", "inc"}, \
        f"스캔된 버전 집합 불일치: {set(versions)}"

    rows = {row["version"]: row for row in payload["versions"]}
    expected = {
        "champ": "champion",
        "arch": "archived",
        "chal": "challenger",
        "inc": "incomplete",
    }
    for ver, want in expected.items():
        assert rows[ver]["bucket"] == want, \
            f"{ver} 버킷이 {want} 아님(우선순위 첫 매치 위반): {rows[ver]['bucket']}"
    # 각 행의 bucket 은 단일 문자열(한 버전=한 버킷)이며 정의된 4종 중 하나
    for ver, row in rows.items():
        assert row["bucket"] in {"champion", "archived", "challenger", "incomplete"}, \
            f"{ver} 의 bucket 이 정의된 4종 밖: {row['bucket']}"


# ===== A5. archived 직접-삽입 변형 — 감사 레코드 직접 append (박을것 #1 대안 셋업) =====
def test_archived_via_direct_audit_append(console):
    """approve 경로 대신 감사 레코드를 직접 삽입해 '과거 활성' 이력을 구성한 archived.

    B1 복원으로 키-표현 확정: 핸드오프 전 구간의 감사 to_version 은 **버전 디렉토리명**
    'gru_<fs>@<v>' 단일 표현이다(approve handoff:148, reconcile/bootstrap handoff:191/204).
    따라서 직접 append 도 디렉토리명(arch_id='gru_vitals@arch')으로 '과거 활성' 이력을 박는다
    — 더는 맨버전 가정이 아니다. classify 의 archived 도출 비교가 같은 디렉토리명 기준이므로
    일치한다.
    """
    fs = "vitals"
    champ_id, _ = console.mk("champ", ready=True)
    arch_id, _ = console.mk("arch", ready=True)     # 비활성 + .ready 보유
    # 감사상 'arch 가 한때 활성이었음' 기록(과거 APPROVE) — to_version=디렉토리명(B1)
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version=None, to_version=arch_id, gate_passed=True)
    console.fd.set_active(fs, champ_id)             # 현재 alias = champ → arch 는 비활성

    rows = _rows_by_version(console.service.list_versions(fs))
    assert rows["champ"]["bucket"] == "champion"
    assert rows["arch"]["bucket"] == "archived", \
        f"감사상 과거 활성·현재 비활성 버전이 archived 아님: {rows['arch']}"
    assert rows["arch"]["bucket"] != "challenger", \
        ".ready 있어도 과거 활성이면 archived 여야(challenger 아님)"
