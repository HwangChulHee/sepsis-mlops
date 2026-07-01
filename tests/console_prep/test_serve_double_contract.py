"""메타테스트 — serve 테스트 대역(_serve_helpers)이 실제 Bundle 계약과 어긋나지 않게 가드.

배경: 가짜 load_bundle_from_dir 가 실제 Bundle 의 input_dim 을 빠뜨려 /health 가 500 으로
회귀한 사고(커밋 e523a86)가 있었다. 대역은 SimpleNamespace 라 타입체커가 누락을 못 잡는다.
이 테스트가 그 회귀 클래스를 막는다 — **serving 이 번들에서 실제로 읽는 필드**가 대역에도
있는지 검증(쓰지도 않는 numpy/model 필드까지 강제하진 않는다 — 과한 결합).

검증 기준: serve/app.py 의 /health 와 reload 경로가 bundle 에서 읽는 스칼라 메타 필드 =
run_id · featureset · input_dim. 이 셋이 대역 출력에 없으면 실패 → 대역을 고치라는 신호.
"""
from __future__ import annotations

import dataclasses

from _serve_helpers import patch_loaders

import sepsis.serve.app as serve_app
from sepsis.serve.bundle import Bundle

# serving 이 번들에서 읽는 스칼라 메타(=/health 응답·reload 로그). 이게 늘어나면 여기 추가.
SERVING_READS = ("run_id", "featureset", "input_dim")


def test_fake_bundle_carries_fields_serving_reads(monkeypatch, tmp_path):
    """대역 load_bundle_from_dir 의 결과가 serving 이 읽는 필드를 모두 노출하는가."""
    patch_loaders(monkeypatch)                       # serve_app.load_bundle_from_dir 를 대역으로
    fake = serve_app.load_bundle_from_dir(tmp_path)   # 대역 호출
    for field in SERVING_READS:
        assert hasattr(fake, field), (
            f"serve 대역 번들에 '{field}' 가 없다 — serving 이 이 필드를 읽으면 500 회귀. "
            f"_serve_helpers.fake_load_bundle_from_dir 를 고쳐라."
        )


def test_serving_read_fields_are_real_bundle_fields():
    """SERVING_READS 가 실제 Bundle dataclass 필드의 부분집합인지(오타·유령 필드 가드)."""
    real_fields = {f.name for f in dataclasses.fields(Bundle)}
    missing = set(SERVING_READS) - real_fields
    assert not missing, f"SERVING_READS 에 실제 Bundle 에 없는 필드: {missing}"
