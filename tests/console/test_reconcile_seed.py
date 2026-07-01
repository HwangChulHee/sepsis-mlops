"""성공기준 7 — 부팅 화해/seed + 거짓 RECONCILE 회귀 방지 + 경계 완전성.

검증 대상(handoff:179-216, 254, 결정 7-1·7-2 / B1·mn1·mn3·B-r5):
- 콜드스타트(감사 빔 + champion 존재) → BOOTSTRAP seed 1건, actor_unverified="system".
- 분기(alias != 감사 last_active) → RECONCILE 1건, from=감사최종·to=실제alias, archived 보존.
- **정상 승인 1회 후 재기동 → RECONCILE 안 생김**(last.to_version·alias 둘 다 디렉토리명, B1).
- alias=None + 이력 존재 → 거짓 복원 없이 경보만(mn3).
- 화해/seed 가 라우팅 개시 *전* 완료(lifespan 순서).

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations


def _count(store, fs):
    return len(store.query(featureset=fs))


# ===== 콜드스타트: BOOTSTRAP seed 1건, actor=system =====
def test_coldstart_seeds_bootstrap(console):
    # 성공기준 7 (mn1) — 감사 빔 + champion 존재 → seed 1건
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")  # 콘솔 이전부터 champion 존재
    assert console.store.last_active(fs) is None

    console.service._reconcile_or_seed(fs)

    ev = console.store.last_active(fs)
    assert ev is not None and ev.event_type == "BOOTSTRAP"
    assert ev.from_version is None
    assert ev.to_version == "gru_vitals@v1"
    assert ev.actor_unverified == "system", "BOOTSTRAP actor 가 system 아님(mn1 회귀)"
    assert _count(console.store, fs) == 1


# ===== 분기: RECONCILE 1건, from=감사최종·to=실제alias, archived 보존 =====
def test_divergence_records_reconcile(console):
    # 성공기준 7 — ②후③전 크래시/콘솔 밖 수동 변경 흔적
    fs = "vitals"
    # 감사상 최종 활성 = v2 (APPROVE 흔적), 그러나 실제 alias = v3 (③ 누락 크래시)
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version="gru_vitals@v1", to_version="gru_vitals@v2",
                         gate_passed=True)
    console.fd.set_active(fs, "gru_vitals@v3")

    console.service._reconcile_or_seed(fs)

    ev = console.store.last_active(fs)
    assert ev.event_type == "RECONCILE"
    assert ev.from_version == "gru_vitals@v2", "from=감사상 직전 최종활성(archived 보존, mn-r5)"
    assert ev.to_version == "gru_vitals@v3", "to=실제 alias(디렉토리명)"
    assert ev.actor_unverified == "system"


# ===== 거짓 RECONCILE 회귀 방지: 정상 승인 후 재기동 시 RECONCILE 없음 (B1) =====
def test_no_false_reconcile_after_normal_approve(console):
    # 성공기준 7 (B1 핵심) — last.to_version·alias_target 둘 다 디렉토리명이라 일치
    fs = "vitals"
    # 정상 승인 흔적: 감사 to_version 과 alias 가 같은 디렉토리명
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version="gru_vitals@v2", to_version="gru_vitals@v3",
                         gate_passed=True)
    console.fd.set_active(fs, "gru_vitals@v3")
    before = _count(console.store, fs)

    console.service._reconcile_or_seed(fs)  # 재기동 화해

    assert _count(console.store, fs) == before, "거짓 RECONCILE 발생(B1 회귀 — 맨버전 vs 디렉토리명)"


# ===== alias=None + 이력 존재 → 거짓 복원 없이 경보만 (mn3) =====
def test_missing_alias_with_history_alerts_only(console):
    # 성공기준 7 (mn3) — champion 부재(심링크 소실)인데 이력 존재: 감사 날조 금지
    fs = "vitals"
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version=None, to_version="gru_vitals@v1", gate_passed=True)
    # alias 없음(active_version → None)
    assert console.fd.active_version(fs) is None
    before = _count(console.store, fs)

    alerts = []
    console.monkeypatch.setattr(console.service, "_alert_missing_alias",
                               lambda f: alerts.append(f), raising=False)

    console.service._reconcile_or_seed(fs)

    assert _count(console.store, fs) == before, "alias 소실인데 감사 레코드를 날조함(mn3 위반)"
    assert alerts == [fs], "운영자 경보(_alert_missing_alias)가 불리지 않음"


# ===== 빈 시스템(감사 빔 + alias 없음) → 무동작 =====
def test_empty_system_no_op(console):
    # 성공기준 7 — champion 도 이력도 없으면 아무 레코드도 안 생김
    fs = "vitals"
    console.service._reconcile_or_seed(fs)
    assert _count(console.store, fs) == 0


# ===== lifespan: 화해/seed 가 라우팅 개시(yield) 전에 완료 =====
def test_lifespan_reconciles_before_routing(console):
    # 성공기준 7 (결정 7-1 경계 완전성) — yield 전에 _reconcile_or_seed 완료
    # [검증 필요] 선행: service.lifespan(asynccontextmanager)·CONSOLE_FEATURESETS 노출
    import asyncio

    svc = console.service
    fsets = getattr(svc, "CONSOLE_FEATURESETS", None)
    if fsets is None:
        from sepsis.console.config import CONSOLE_FEATURESETS as fsets  # noqa: N811

    calls = []
    console.monkeypatch.setattr(svc, "_reconcile_or_seed",
                               lambda fs: calls.append(fs), raising=False)

    async def run():
        async with svc.lifespan(object()):
            # __aenter__(reconcile 루프) 완료 후 body 진입 = 라우팅 개시 직전
            assert calls == list(fsets), f"라우팅 전 화해 미완료: calls={calls}"

    asyncio.run(run())
    assert calls == list(fsets)
