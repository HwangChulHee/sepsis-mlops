"""부하테스트 드라이버 — TDD RED (핸드오프 load-test §3 T1~T6).

출제자-응시자 분리: 이 파일은 **핸드오프만** 근거로 작성됐다. 구현 코드
(`src/`·`bench/`)를 읽지 않았다. 대상 신규 모듈(`bench/load/patient_pool.py`·
`bench/load/request_builder.py`)이 아직 없으므로 모든 테스트는 **RED**여야 한다.

계약 근거(핸드오프 load-test/handoff.md):
  - §2.1 PatientPool: setB PSV 풀에서 미사용 환자를 배타적으로 하나씩 반환.
      `claim() -> Path | None`. 반복(재사용) 금지, 소진 시 None(예외 아님),
      스레드세이프. `total`·`remaining` 조회 가능. 소스 디렉토리 주입 가능
      (기본 setB, 테스트는 tmp 합성 .psv).
  - §2.2 request_builder: PSV 행 dict + patient_id → `/predict` 페이로드
      `{"patient_id": str, "features": {col: float|None}}`. NaN→None **보존**
      (0/평균 채움 금지). features 키는 featureset 컬럼 부분집합.
  - §3 주의: 실 setB 20,000 파일 의존 금지 — tmp에 소형 합성 .psv(파이프 `|`
      구분·헤더·NaN 셀 포함)를 fixture로 만들어 검증.

누수 방지 대원칙(CLAUDE.md)과의 연결: T5는 "0으로 채우지 않음"(의료 결측 보존)
불변식을 요청 조립 경계에서 고정한다 — NaN 셀이 0/평균으로 둔갑하면 실패.

임포트 경로는 핸드오프 §1·spec-writer 지시에 따름:
  from loadtest.patient_pool import PatientPool
  from loadtest.request_builder import build_predict_payload
대상 모듈이 없으면 각 테스트 안의 import에서 ModuleNotFoundError → **의도된 RED**
(로그에 "구현 없음"이 분명히 남게 함수 내부 import).

[가정] (핸드오프가 시그니처를 문자 그대로 못박지 않은 부분 — docstring에 명시):
  - PatientPool은 소스 디렉토리를 **첫 위치 인자**로 받는다: `PatientPool(dir)`.
    (§2.1 "소스 디렉토리를 주입 가능하게".) 키워드명은 미확정이라 위치 인자 사용.
  - build_predict_payload(row, patient_id) — 행 dict가 첫 인자, patient_id가
    둘째(§2.2 "PSV 행 dict + patient_id" 순서). patient_id는 키워드로 전달해
    인자 순서 오해를 줄인다.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

# --- 합성 .psv fixture 헬퍼 -------------------------------------------------
# PhysioNet/CinC PSV: 파이프(|) 구분, 첫 줄 헤더, 결측은 "NaN" 리터럴 셀.
# featureset "vitals"(9컬럼) 중 대표 컬럼을 사용한다 — 정확한 9개 컬럼명은
# config(FEATURESET_VITALS)의 권위이며 여기서 읽지 않는다. 테스트가 고정하는
# 관측 계약은 "빌더가 입력 featureset 행을 그대로 통과시키고 키를 더/덜하지
# 않는다"이므로 대표 컬럼으로 충분하다.
_VITALS_HEADER = ["HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp"]


def _write_psv(path: Path, rows: list[dict]) -> None:
    """rows(각 dict: col->값 또는 None) 를 파이프 구분 .psv로 쓴다.

    None 셀은 "NaN" 리터럴로 기록(PhysioNet 관례). 헤더는 _VITALS_HEADER 순서.
    """
    lines = ["|".join(_VITALS_HEADER)]
    for r in rows:
        cells = []
        for col in _VITALS_HEADER:
            v = r.get(col, None)
            cells.append("NaN" if v is None else repr(float(v)))
        lines.append("|".join(cells))
    path.write_text("\n".join(lines) + "\n")


def _make_pool_dir(tmp_path: Path, k: int) -> Path:
    """tmp에 K개의 서로 다른 소형 합성 .psv를 만들고 그 디렉토리를 돌려준다."""
    d = tmp_path / "setB_synthetic"
    d.mkdir()
    for i in range(k):
        _write_psv(
            d / f"p{i:05d}.psv",
            rows=[{"HR": 80 + i, "O2Sat": 97.0}, {"HR": 81 + i, "O2Sat": None}],
        )
    return d


# ===========================================================================
# T1 — 배타 배정 (핸드오프 §3 T1 / 결정 M1 배타 distinct)
# ===========================================================================
def test_t1_claim_returns_distinct_files():
    """연속 claim() N회 → 서로 다른 파일 N개(중복 0)."""
    from loadtest.patient_pool import PatientPool

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = _make_pool_dir(Path(tmp), k=10)
        pool = PatientPool(d)
        claimed = [pool.claim() for _ in range(10)]

    assert all(c is not None for c in claimed), "10개 풀에서 10회 claim은 모두 성공해야 한다"
    paths = [Path(c) for c in claimed]
    assert len(paths) == len(set(paths)), f"claim 결과에 중복 파일 존재: {paths}"


# ===========================================================================
# T2 — 반복 금지 (핸드오프 §3 T2 / 결정 B1 비반복)
# ===========================================================================
def test_t2_no_repeat_beyond_pool_size():
    """풀 크기 K면 claim은 최대 K회 성공 후 None — 이미 claim된 파일 재claim 안 됨."""
    from loadtest.patient_pool import PatientPool

    import tempfile

    K = 7
    with tempfile.TemporaryDirectory() as tmp:
        d = _make_pool_dir(Path(tmp), k=K)
        pool = PatientPool(d)
        successful = []
        for _ in range(K):
            c = pool.claim()
            assert c is not None, "풀 소진 전 claim은 성공해야 한다"
            successful.append(Path(c))
        # K회 이후 추가 claim은 None (더 줄 미사용 환자 없음)
        assert pool.claim() is None, "K회 성공 이후 claim은 None이어야 한다(비반복·유한)"

    assert len(set(successful)) == K, "K회 claim 결과는 모두 서로 다른 파일이어야 한다(반복 금지)"


# ===========================================================================
# T3 — 고갈 (핸드오프 §3 T3 / 결정 B1-r2 유한)
# ===========================================================================
def test_t3_exhaustion_returns_none_not_exception():
    """풀 소진 후 claim() → None(예외 아님)."""
    from loadtest.patient_pool import PatientPool

    import tempfile

    K = 3
    with tempfile.TemporaryDirectory() as tmp:
        d = _make_pool_dir(Path(tmp), k=K)
        pool = PatientPool(d)
        for _ in range(K):
            assert pool.claim() is not None

        # 소진 후 여러 번 호출해도 예외 없이 None을 돌려준다.
        for _ in range(3):
            assert pool.claim() is None, "소진 후 claim은 예외가 아니라 None이어야 한다"


# ===========================================================================
# T4 — 스레드세이프 (핸드오프 §3 T4 / 배타 배정 동시성)
# ===========================================================================
def test_t4_thread_safe_no_double_claim():
    """여러 스레드 동시 claim해도 같은 파일이 두 번 안 나옴(중복 0)."""
    from loadtest.patient_pool import PatientPool

    import tempfile

    K = 200
    n_threads = 16
    with tempfile.TemporaryDirectory() as tmp:
        d = _make_pool_dir(Path(tmp), k=K)
        pool = PatientPool(d)

        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(n_threads)  # 동시 출발로 경합 유발

        def worker():
            barrier.wait()
            local = []
            while True:
                c = pool.claim()
                if c is None:
                    break
                local.append(Path(c))
            with results_lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 모든 스레드가 나눠 가진 파일의 합집합에 중복이 없어야 하고, 정확히 K개.
    assert len(results) == len(set(results)), "동시 claim에서 같은 파일이 두 번 배정됨(경합 버그)"
    assert len(set(results)) == K, f"배타 배정 총합이 풀 크기와 불일치: {len(set(results))} != {K}"


# ===========================================================================
# T5 — 요청 스키마 조립 (핸드오프 §3 T5 / 결측 계약·스키마)
#      누수 방지 대원칙: 결측(NaN/None)을 0/평균으로 채우지 않는다.
# ===========================================================================
def test_t5_build_predict_payload_schema_and_none_preserved():
    """행 dict + patient_id → {"patient_id": str, "features": {...}}.

    - patient_id는 str로 페이로드에 보존.
    - features는 입력 행의 값을 그대로 — None(결측)은 None으로 보존(0/평균 금지).
    - features 키는 입력 featureset 행 키의 부분집합(빌더가 키를 새로 만들지 않음).
    """
    from loadtest.request_builder import build_predict_payload

    row = {"HR": 88.0, "O2Sat": None, "Temp": 37.2, "MAP": None}

    payload = build_predict_payload(row, patient_id="p00042")

    # 최상위 스키마
    assert set(payload.keys()) == {"patient_id", "features"}, f"페이로드 최상위 키 불일치: {payload.keys()}"
    assert isinstance(payload["patient_id"], str)
    assert payload["patient_id"] == "p00042"

    features = payload["features"]
    assert isinstance(features, dict)

    # None(결측) 보존 — 0/평균으로 둔갑하면 실패(누수 방지 대원칙)
    assert features["O2Sat"] is None, "결측 O2Sat이 None으로 보존돼야 한다(0/평균 금지)"
    assert features["MAP"] is None, "결측 MAP이 None으로 보존돼야 한다(0/평균 금지)"

    # 실측값 보존
    assert features["HR"] == 88.0
    assert features["Temp"] == 37.2

    # 빌더는 입력 featureset 행 키를 초과하는 키를 만들지 않는다(⊆ featureset 컬럼).
    assert set(features.keys()) <= set(row.keys()), "features에 입력 행에 없는 키가 추가됨"


# ===========================================================================
# T6 — PSV 순서 보존 (핸드오프 §3 T6 / causal 순서)
# ===========================================================================
def test_t6_row_order_preserved_through_builder():
    """파일 행 순서를 재배치 없이 그대로 산출.

    [가정] 핸드오프 §2.3·§7: 소스는 파일=시간순으로 행을 산출하고, 빌더 경로는
    재배치하지 않는다(causal). 여기서는 파일 순서로 정렬된 행 시퀀스를 빌더에
    통과시켜 출력 페이로드 시퀀스가 입력 순서를 그대로 유지하는지 고정한다.
    순서 신호를 feature 값(HR = 0,1,2,...)에 인코딩해, 재배치가 일어나면 실패하게 한다.
    """
    from loadtest.request_builder import build_predict_payload

    # 파일 순서(시간순)를 흉내낸 행 시퀀스 — HR가 단조 증가(순서 신호).
    rows_in_file_order = [
        {"HR": float(i), "O2Sat": (None if i % 2 else 95.0 + i)}
        for i in range(8)
    ]

    payloads = [
        build_predict_payload(r, patient_id="pOrder") for r in rows_in_file_order
    ]

    # 출력 순서가 입력(파일) 순서와 동일해야 한다 — 재배치 금지.
    observed_hr = [p["features"]["HR"] for p in payloads]
    assert observed_hr == [float(i) for i in range(8)], (
        f"행 순서가 재배치됨(causal 위반): {observed_hr}"
    )

    # 순서와 함께 결측 패턴도 보존(홀수 인덱스 O2Sat = None)
    for i, p in enumerate(payloads):
        if i % 2:
            assert p["features"]["O2Sat"] is None, f"index {i} 결측이 순서 보존 중 훼손됨"
