"""성공기준 3 — versions 분류: champion(alias)/challenger/incomplete + alias 권위.

검증 대상(handoff:119-128, 250, 결정 1·5·7-2):
- .ready 있는 비활성 = challenger, 없으면 incomplete, active_version 타겟 = champion.
- champion 판정이 alias(FS)에서 나오고 **감사 DB 추정이 아님**.

읽기 엔드포인트는 GET /console/versions?fs=vitals (핸드오프가 명세한 4-버킷:
champion/challenger/incomplete/archived). 함수명은 핸드오프에 안 박혀 엔드포인트로 검증.

[검증 필요] 선행:
- sepsis.console.api.app(FastAPI app) 노출 + /console/versions 엔드포인트.
- api 가 요청 시점에 service.ARTIFACTS·service.audit·deploy.active_version(대역)을 사용.
- 응답 JSON 의 4-버킷 키명(champion/challenger/incomplete/archived) — handoff:123 명문.

src/ 구현 코드는 읽지 않았다.
"""
from __future__ import annotations

import pytest


def _collect_version_ids(node):
    """응답 노드(스칼라/list/dict) 트리에서 gru_ 로 시작하는 모든 식별자 문자열 수집.

    버킷 값이 문자열·문자열 리스트·객체 리스트 어느 형태든 견디게(스키마 변동 흡수).
    """
    out = set()
    if isinstance(node, str):
        if node.startswith("gru_"):
            out.add(node)
    elif isinstance(node, dict):
        for v in node.values():
            out |= _collect_version_ids(v)
    elif isinstance(node, list):
        for v in node:
            out |= _collect_version_ids(v)
    return out


@pytest.fixture
def client(console):
    try:
        import sepsis.console.api as api
        from fastapi.testclient import TestClient
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"[검증 필요] console.api.app 미정의 — 구현 후 RED→GREEN: {e}")
    # api 가 service 전역을 참조하도록(요청 시점 위임) — console fixture 가 이미 패치함
    with TestClient(api.app) as c:
        yield c


def _get_versions(client, fs="vitals"):
    r = client.get(f"/console/versions?fs={fs}")
    assert r.status_code == 200, f"/console/versions 비정상 응답: {r.status_code} {r.text}"
    return r.json()


# ===== champion=alias / challenger=ready-비활성 / incomplete=non-ready =====
def test_versions_classify_three_buckets(console, client):
    # 성공기준 3
    fs = "vitals"
    champ, _ = console.mk("champ", ready=True)
    chal, _ = console.mk("chal", ready=True)
    inc, _ = console.mk("inc", ready=False)   # .ready 없음 = 미완성
    console.fd.set_active(fs, champ)           # alias = champion

    body = _get_versions(client, fs)
    assert "champion" in body and "challenger" in body and "incomplete" in body

    champ_ids = _collect_version_ids(body["champion"])
    chal_ids = _collect_version_ids(body["challenger"])
    inc_ids = _collect_version_ids(body["incomplete"])

    assert champ in champ_ids, f"champion 에 alias 버전 누락: {champ_ids}"
    assert chal in chal_ids, f"ready-비활성이 challenger 아님: {chal_ids}"
    assert inc in inc_ids, f".ready 없는 버전이 incomplete 아님: {inc_ids}"
    # 미완성은 challenger 로 새지 않는다(두-파일 AND/.ready 게이트)
    assert inc not in chal_ids


# ===== champion 은 alias 권위 — 감사 DB 추정이 아님 (결정 7-2) =====
def test_champion_from_alias_not_audit_db(console, client):
    # 성공기준 3 — 감사 last_active 와 alias 가 갈라져도 champion 은 alias 를 따른다
    fs = "vitals"
    champ, _ = console.mk("champ", ready=True)
    other, _ = console.mk("other", ready=True)
    # 감사상 최종 활성을 other 로 심어 둠(만약 champion 을 감사로 추정하면 other 가 나올 것)
    console.store.append(event_type="APPROVE", featureset=fs,
                         from_version=None, to_version=other, gate_passed=True)
    console.fd.set_active(fs, champ)  # 그러나 실제 alias = champ

    body = _get_versions(client, fs)
    champ_ids = _collect_version_ids(body["champion"])
    assert champ in champ_ids, "champion 이 alias(champ)가 아님"
    assert other not in champ_ids, "champion 을 감사 DB 로 추정함(alias 권위 위반 — 결정 7-2)"
