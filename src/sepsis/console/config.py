"""콘솔 설정 상수 (R2 minor 2건 — 단일 출처).

`CONSOLE_FEATURESETS`: lifespan 화해·버전 스캔이 도는 featureset 목록. 기본 ["vitals"],
환경변수 `CONSOLE_FEATURESETS`(쉼표 구분)로 override.
"""
from __future__ import annotations

import os

CONSOLE_FEATURESETS: list[str] = [
    s.strip() for s in os.environ.get("CONSOLE_FEATURESETS", "vitals").split(",") if s.strip()
]
