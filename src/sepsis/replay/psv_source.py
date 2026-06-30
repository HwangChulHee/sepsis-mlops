""".psv 어댑터 — PhysioNet 환자 파일을 읽어 featureset 행을 시간순으로 내준다 (핸드오프 §3.2).

계약(§3.2·§5.6):
- pandas read_csv(sep="|"), 헤더 있음 (data/cache.py 와 동일 방식).
- C.featureset_columns(featureset) 컬럼만 선택 — 비-feature(SepsisLabel·ICULOS·EtCO2 등) 제외(F3 근거).
- NaN/빈 셀 → None (0/평균 채움 금지, F1). 측정값은 raw float 그대로(전처리는 서버 몫, §2·§5.7).
- 파일 순서 그대로 yield (정렬·재배치 금지, F2).
- patient_id: 명시값 우선, 없으면 파일 stem. run_suffix 주면 "{base}-{run_suffix}".

F4(재실행 stale state): 서버엔 리셋 엔드포인트가 없어, 같은 patient_id 로 다시 틀면
서버가 이전 실행의 hidden state 를 이어받아 곡선이 오염된다. CLI 가 run 마다 유일한
run_suffix 를 만들어 patient_id 를 새로 찍는 것으로 회피한다(이 클래스는 그 훅만 제공).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from sepsis import config as C


class PsvRowSource:
    """한 환자 .psv → featureset 행 스트림. RowSource 프로토콜(patient_id + __iter__) 충족."""

    def __init__(
        self,
        path,
        featureset: str = "vitals",
        patient_id: str | None = None,
        run_suffix: str | None = None,
    ):
        self.path = Path(path)
        self.featureset = featureset
        self._cols = C.featureset_columns(featureset)   # featureset 밖 키는 안 신뢰(F3)

        base = patient_id if patient_id is not None else self.path.stem
        self.patient_id = f"{base}-{run_suffix}" if run_suffix is not None else base

        # 파일을 한 번 읽어 featureset 컬럼만, NaN→None, 파일 순서로 행 리스트화.
        df = pd.read_csv(self.path, sep="|")            # 파이프 구분, 헤더 있음
        sub = df[self._cols]                            # featureset 컬럼만 — 비-feature 탈락
        self._rows: list[dict[str, float | None]] = []
        for rec in sub.to_dict(orient="records"):       # 파일(=시간) 순서 보존
            # NaN/결측 → None, 그 외 raw float (정규화·clip·ffill 일절 없음, §5.7)
            self._rows.append(
                {c: (None if pd.isna(v) else float(v)) for c, v in rec.items()}
            )

    def __iter__(self):
        return iter(self._rows)
