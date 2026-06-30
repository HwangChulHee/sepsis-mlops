"""리플레이어 라운드 (가) — .psv 어댑터 TDD RED.

권위 출처(이것만 신뢰): design/replay/handoff_round_a.md.
**src/sepsis/replay/ 구현 코드는 일절 읽지 않았다** — 핸드오프 §3.2(어댑터 계약)·
§5.6(.psv 합격기준)·§5.7(정직성)이 못 박은 계약을 그대로 신뢰해 작성한다.
구현(src/sepsis/replay/psv_source.py)이 아직 없으니 지금은 RED 가 정상이다.

신뢰 근거(핸드오프가 명문화한 신규 심볼·계약):
- sepsis.replay.psv_source.PsvRowSource(
      path, featureset="vitals", patient_id=None, run_suffix=None)   (handoff §3.2:99-110)
    · pandas read_csv(sep="|"), 헤더 있음
    · C.featureset_columns(featureset) 컬럼만 선택 (SepsisLabel 등 비-feature 제외)
    · NaN → None (0/평균 채움 금지)
    · 파일 순서 그대로 yield (정렬·재배치 금지)
    · patient_id: 명시값 우선, 없으면 파일 stem, run_suffix 시 "{stem}-{run_suffix}"
허용된 기존 공개 의존성(SUT 아님): sepsis.config.featureset_columns (handoff §2·§3).

이 파일이 커버하는 합격기준(handoff §5): 6(.psv 어댑터), 7(정직성·전처리 부재).
"""

from __future__ import annotations

from sepsis import config as C
from sepsis.replay.psv_source import PsvRowSource


VITALS = C.featureset_columns("vitals")  # 9 cols, no labs/EtCO2/label


def write_psv(tmp_path, name: str, header: list[str], rows: list[list[str]]):
    """파이프 구분·헤더 있는 PhysioNet 스타일 .psv 를 tmp_path 에 작성하고 경로 반환."""
    path = tmp_path / name
    lines = ["|".join(header)]
    lines += ["|".join(cells) for cells in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


# .psv 헤더: featureset(vitals 9) + 비-feature 컬럼들(제외되어야 함)
PSV_HEADER = VITALS + ["WBC", "ICULOS", "SepsisLabel"]


def make_rows():
    """2개 타임스텝. 두 번째 행의 SBP 는 NaN(결측) 셀."""
    # vitals 9개 raw 값
    v0 = [str(float(i + 1)) for i in range(len(VITALS))]
    v1 = [str(float(i + 10)) for i in range(len(VITALS))]
    # SBP 는 index 3 (HR,O2Sat,Temp,SBP,...) — 두 번째 행에서 NaN 으로
    sbp_idx = VITALS.index("SBP")
    v1[sbp_idx] = "NaN"
    # 비-feature 꼬리: WBC, ICULOS, SepsisLabel
    row0 = v0 + ["7.5", "1", "0"]
    row1 = v1 + ["NaN", "2", "1"]
    return [row0, row1]


# --------------------------------------------------------------------------
# 기준 6 — .psv 어댑터   handoff §5.6:147 / §3.2
# --------------------------------------------------------------------------

def test_yields_only_featureset_columns(tmp_path):
    """featureset(vitals) 컬럼만 나온다 — 비-feature(WBC/ICULOS/SepsisLabel) 미포함."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    rows = list(src)
    allowed = set(VITALS)
    for r in rows:
        assert set(r.keys()) == allowed
    # 명시적으로 비-feature 키 부재 확인
    for r in rows:
        assert "WBC" not in r
        assert "ICULOS" not in r
        assert "SepsisLabel" not in r


def test_nan_cell_becomes_none(tmp_path):
    """NaN 셀 → None (0/평균 채움 금지)."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    rows = list(src)
    assert rows[1]["SBP"] is None
    assert rows[1]["SBP"] != 0.0


def test_present_cells_are_raw_floats(tmp_path):
    """결측 아닌 셀은 raw float 그대로."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    rows = list(src)
    # 첫 행 HR(index0) = 1.0
    assert rows[0]["HR"] == 1.0
    # 두 번째 행 HR = 10.0 (SBP만 NaN, 나머지는 raw)
    assert rows[1]["HR"] == 10.0


def test_row_order_preserved(tmp_path):
    """파일 순서 그대로 yield (시간순; 정렬·재배치 금지)."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    rows = list(src)
    assert len(rows) == 2
    # HR 로 순서 확인: 행0=1.0, 행1=10.0
    assert [r["HR"] for r in rows] == [1.0, 10.0]


def test_patient_id_defaults_to_file_stem(tmp_path):
    """patient_id 미지정 → 파일 stem."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    assert src.patient_id == "p000023"


def test_patient_id_explicit_overrides_stem(tmp_path):
    """명시 patient_id 우선."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals", patient_id="custom-id")

    assert src.patient_id == "custom-id"


def test_run_suffix_appended_to_stem(tmp_path):
    """run_suffix 주어지면 '{stem}-{run_suffix}' (F4: stale state 회피)."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals", run_suffix="abc123")

    assert src.patient_id == "p000023-abc123"


def test_vitals_labs_featureset_selects_18_columns(tmp_path):
    """featureset 인자를 그대로 신뢰 — vitals_labs 면 18개 컬럼만 (EtCO2/label 제외)."""
    cols_18 = C.featureset_columns("vitals_labs")
    header = cols_18 + ["EtCO2", "ICULOS", "SepsisLabel"]
    v = [str(float(i + 1)) for i in range(len(cols_18))]
    row = v + ["33.0", "1", "0"]
    path = write_psv(tmp_path, "p000099.psv", header, [row])

    src = PsvRowSource(path, featureset="vitals_labs")
    rows = list(src)

    assert set(rows[0].keys()) == set(cols_18)
    assert "EtCO2" not in rows[0]
    assert "SepsisLabel" not in rows[0]


# --------------------------------------------------------------------------
# 기준 7 — 정직성: 전처리 부재   handoff §5.7:148 / §2:44
# --------------------------------------------------------------------------

def test_no_ffill_missing_stays_none(tmp_path):
    """결측이 직전 값으로 ffill 되지 않음 — None 유지 (서버가 ffill 함)."""
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, make_rows())
    src = PsvRowSource(path, featureset="vitals")

    rows = list(src)
    # 행1 SBP NaN — 행0 SBP(raw)로 메우지 않았는지
    sbp0 = rows[0]["SBP"]
    assert rows[1]["SBP"] is None
    assert rows[1]["SBP"] != sbp0


def test_no_clip_out_of_range_value_preserved(tmp_path):
    """clip 범위 밖 값도 그대로 — 어댑터는 clip 안 함."""
    # HR 9999.0 은 CLIP_BOUNDS["HR"]=(0,300) 밖
    v = [str(float(i + 1)) for i in range(len(VITALS))]
    v[VITALS.index("HR")] = "9999.0"
    row = v + ["7.5", "1", "0"]
    path = write_psv(tmp_path, "p000023.psv", PSV_HEADER, [row])

    src = PsvRowSource(path, featureset="vitals")
    rows = list(src)

    assert rows[0]["HR"] == 9999.0
