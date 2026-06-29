"""성공기준 6 — 직렬화 경계 + 식별자 규약 + archived 사슬 무결.

검증 대상(handoff:131-157, 253, 결정 7-1·B1):
- 같은 featureset 동시 approve 2건이 featureset 단위 락으로 직렬화 →
  둘째가 갱신된 active 를 prev 로 읽음(prev 갈라짐 없음).
- 감사가 gru_<fs>@V1→V2 · V2→V3 로 이어지고 V1→V3 오염 없음(전부 디렉토리명).
- archived 링크: 앞 레코드 to == 다음 레코드 from 으로 끊김 없이 이어짐.

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import threading


# ===== 동시 approve 2건 직렬화 — prev 갈라짐 없음, 사슬 V1→V2→V3 =====
def test_concurrent_approve_serialized_no_prev_split(console):
    # 성공기준 6 (결정 7-1) — 임계구간이 prev=active 읽기를 직렬화
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    console.fd.swap_delay = 0.15      # 임계구간 내 지연 → 경합 노출(락 없으면 둘 다 v1 읽음)
    v2, _ = console.mk("v2")
    v3, _ = console.mk("v3")

    barrier = threading.Barrier(2)
    errors = []

    def do(version_id):
        try:
            barrier.wait()
            console.service.approve(fs, version_id, actor="op")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t2 = threading.Thread(target=do, args=(v2,))
    t3 = threading.Thread(target=do, args=(v3,))
    t2.start(); t3.start()
    t2.join(); t3.join()
    assert not errors, f"동시 승인 중 예외: {errors}"

    rows = sorted(console.store.query(featureset=fs, event_type="APPROVE"),
                  key=lambda r: r.id)
    assert len(rows) == 2
    froms = [r.from_version for r in rows]
    tos = [r.to_version for r in rows]
    # 직렬화 성립: from 들이 {v1, v2} (둘 다 v1 이 아님 = prev 갈라짐 없음)
    assert set(froms) == {"gru_vitals@v1", "gru_vitals@v2"}, f"prev 갈라짐: froms={froms}"
    # 사슬: V1→V2 · V2→V3 존재, V1→V3 오염 없음
    pairs = set(zip(froms, tos))
    assert ("gru_vitals@v1", "gru_vitals@v2") in pairs
    assert ("gru_vitals@v2", "gru_vitals@v3") in pairs
    assert ("gru_vitals@v1", "gru_vitals@v3") not in pairs, "V1→V3 오염(직렬화 실패)"


# ===== archived 사슬: to == 다음 from 으로 끊김 없이 이어짐 (순차 승인) =====
def test_sequential_approve_chain_links(console):
    # 성공기준 6 (B1·결정 1 archived) — 디렉토리명 단일 표현이라 링크가 어긋나지 않음
    fs = "vitals"
    console.fd.set_active(fs, "gru_vitals@v1")
    for label in ("v2", "v3"):
        console.mk(label)
        console.service.approve(fs, f"gru_vitals@{label}", actor="op")

    rows = sorted(console.store.query(featureset=fs, event_type="APPROVE"),
                  key=lambda r: r.id)
    assert [r.from_version for r in rows] == ["gru_vitals@v1", "gru_vitals@v2"]
    assert [r.to_version for r in rows] == ["gru_vitals@v2", "gru_vitals@v3"]
    # 끊김 없는 사슬: rows[i].to == rows[i+1].from
    assert rows[0].to_version == rows[1].from_version
