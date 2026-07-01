# scripts/data — 데이터 준비 (H1)

원천 PhysioNet .psv → 캐시·전처리·EDA. 파이프라인의 시작점.

| 스크립트 | 한 일 |
|---|---|
| `download_data.sh` | PhysioNet set A+B 다운로드 |
| `eda.py` | 원천 데이터 측정(EDA) → `docs/reports/eda_findings.md` |
| `build_cache.py` | H1-a: NaN 보존 원천 캐시 빌드 + 5 PASS 게이트 |
| `build_dataset.py` | H1-b: 전처리(분할·정규화·시퀀스) + 10-assert 게이트 |
| `run_diagnostics.py` | H1-c: 진단 EDA → `docs/reports/h1_diagnostics.md` |

실행: `uv run python -m scripts.data.build_cache`
