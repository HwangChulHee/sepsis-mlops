"""성공기준 8 — 전파 확인 폴링: /health.run_id == 현재 alias run_id.

검증 대상(handoff:221-240, 255, 결정 2-A / MJ-r5·B2):
- _propagate_and_confirm: /health.run_id == 현재 alias 타겟 dir 의 meta.json.run_id 면 confirmed,
  타임아웃이면 pending.
- 타겟 = "그 swap 의 버전"이 아니라 "현재 alias"(연속 승인 시 옛 버전 거짓 실패 없음).
- 이중접두사 경로(gru_<fs>@gru_<fs>@v) 안 생긴다(_version_dir 사용, B2).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import pytest


def _require_real(console):
    if console.real_propagate is None:
        pytest.skip("[검증 필요] service._propagate_and_confirm 미정의 — 구현 후 RED→GREEN")
    return console.real_propagate


# ===== /health.run_id == 현재 alias run_id → confirmed =====
def test_confirmed_when_health_matches_alias_run_id(console):
    # 성공기준 8 — 서빙이 alias 를 따라잡으면 confirmed
    fs = "vitals"
    v, _ = console.mk("v3", run_id="run-v3")
    console.fd.set_active(fs, v)  # 현재 alias = gru_vitals@v3, run_id=run-v3

    console.monkeypatch.setattr(console.service, "_trigger_reload",
                               lambda f: None, raising=False)
    console.monkeypatch.setattr(console.service, "_get_health",
                               lambda f: {"run_id": "run-v3"}, raising=False)

    result = _require_real(console)(fs, timeout_s=2, interval_s=0.05)
    assert result == "confirmed"


# ===== 타임아웃 → pending =====
def test_pending_on_timeout(console):
    # 성공기준 8 — /health 가 옛 run_id 만 보고하면 타임아웃 후 pending
    fs = "vitals"
    v, _ = console.mk("v3", run_id="run-v3")
    console.fd.set_active(fs, v)

    console.monkeypatch.setattr(console.service, "_trigger_reload",
                               lambda f: None, raising=False)
    console.monkeypatch.setattr(console.service, "_get_health",
                               lambda f: {"run_id": "OLD-run-v2"}, raising=False)

    result = _require_real(console)(fs, timeout_s=0.2, interval_s=0.05)
    assert result == "pending"


# ===== 타겟 = 현재 alias(연속 승인 시 옛 버전 거짓 실패 없음, MJ-r5) =====
def test_target_is_current_alias_not_stale_swap_version(console):
    # 성공기준 8 (MJ-r5) — A(→v2)·B(→v3) 순차 통과 후 alias=v3.
    # _propagate_and_confirm 은 *현재 alias(v3)* 를 타겟으로 → /health 가 v3 면 confirmed.
    # (옛 swap 버전 v2 를 타겟으로 박으면 A 의 폴링이 영원히 pending — 그 회귀를 차단)
    fs = "vitals"
    console.mk("v2", run_id="run-v2")
    v3, _ = console.mk("v3", run_id="run-v3")
    console.fd.set_active(fs, v3)  # 연속 승인 수렴점 = v3

    console.monkeypatch.setattr(console.service, "_trigger_reload",
                               lambda f: None, raising=False)
    console.monkeypatch.setattr(console.service, "_get_health",
                               lambda f: {"run_id": "run-v3"}, raising=False)

    # v2 의 폴링도 "현재 alias(v3)" 기준 → confirmed (옛 v2 거짓 실패 없음)
    result = _require_real(console)(fs, timeout_s=2, interval_s=0.05)
    assert result == "confirmed"


# ===== 이중접두사 경로 안 생김 — meta 읽기가 _version_dir(접두사 재부착 없음) (B2) =====
def test_no_double_prefix_in_meta_path(console):
    # 성공기준 8 (B2) — 타겟 dir 의 meta.json 을 ARTIFACTS/<dirname> 으로 직접 경로화.
    # 이중접두사면 _read_meta 가 빈 dict → run_id None → 영원히 pending 이 되었을 것.
    fs = "vitals"
    v, _ = console.mk("v3", run_id="run-v3")
    console.fd.set_active(fs, v)

    seen_paths = []
    real_read_meta = console.service._read_meta
    def spy_read_meta(path):
        seen_paths.append(str(path))
        return real_read_meta(path)
    console.monkeypatch.setattr(console.service, "_read_meta", spy_read_meta, raising=False)
    console.monkeypatch.setattr(console.service, "_trigger_reload",
                               lambda f: None, raising=False)
    console.monkeypatch.setattr(console.service, "_get_health",
                               lambda f: {"run_id": "run-v3"}, raising=False)

    _require_real(console)(fs, timeout_s=2, interval_s=0.05)
    assert any("gru_vitals@v3" in p for p in seen_paths), "타겟 meta 경로가 안 잡힘"
    assert all("gru_vitals@gru_vitals@" not in p for p in seen_paths), \
        "이중접두사 경로 발생(B2 회귀)"
