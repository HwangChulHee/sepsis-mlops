"""request_builder — PSV 행 dict + patient_id → /predict 페이로드.

설계 근거(docs/design/load-test/): 결정 2·핸드오프 §2.2.
- 페이로드 = {"patient_id": str, "features": {col: float|None}} [확인됨: app.py PredictRequest].
- **결측 보존(누수 방지 대원칙)**: NaN/None 은 None 그대로 — 0/평균으로 채우지 않는다.
  PsvRowSource 가 이미 {col: None|float} 를 산출하므로 그 dict 를 그대로 통과시킨다.
- features 키는 featureset 컬럼(입력 행 키)의 부분집합 — 빌더는 키를 새로 만들지 않는다.
  (초과 키가 있으면 서버가 422; PsvRowSource(featureset="vitals")는 9컬럼만 담아 자연 충족.)
- 순서 보존: 행 시퀀스를 재배치하지 않는다(causal) — 빌더는 행 단위 변환만 하고,
  시퀀스 순서는 호출자(파일=시간순 iterate)가 유지한다.
"""
from __future__ import annotations


def build_predict_payload(row: dict, patient_id: str) -> dict:
    """한 PSV 행(featureset 컬럼 dict)을 /predict 요청 페이로드로 조립한다.

    row 의 값(None 포함)을 그대로 features 로 담고 patient_id 를 str 로 붙인다.
    행을 얕은 복사해 호출자의 원본을 건드리지 않는다(측정 중 상태 오염 방지).
    """
    return {"patient_id": str(patient_id), "features": dict(row)}
