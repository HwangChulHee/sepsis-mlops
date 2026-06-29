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

import pytest


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
