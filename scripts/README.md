# scripts/ — 실행 스크립트 (단계별)

풀 파이프라인(H1→H4)의 러너·게이트·도구를 **단계별 하위폴더**로 묶었다. 각 스크립트는
자립형 러너이고, 대부분 프로그램적 assert 스모크 게이트를 포함한다(토막마다 PASS 확인).

## 실행 방법

리포지토리 루트에서 **모듈 형식**으로 실행한다(경로 해석은 `sepsis.config.ROOT` 기준이라
스크립트 위치와 무관):

```bash
uv run python -m scripts.<group>.<name>      # 예: uv run python -m scripts.h2.h2b_train_trees
```

## 실행 순서 (전체 재현)

```
data → h2 → h3 → h4          (replay·tools 는 파이프라인과 독립)
```

## 그룹

| 그룹 | 목적 | 스크립트 |
|---|---|---|
| [`data/`](data/) | 원천 데이터 → 캐시·피처·EDA (H1) | build_cache · build_dataset · eda · run_diagnostics · download_data.sh |
| [`h2/`](h2/) | 학습: 트리 vs GRU + ablation·선택 | h2a_utility_check · h2b_train_trees · h2c_train_gru · h2d_select · smoke_m2m |
| [`h3/`](h3/) | cross-site 평가 A→B (B 개봉)·마스크 ablation | h3b_crosssite · h3c_mask_check |
| [`h4/`](h4/) | 운영: 서빙·드리프트·재학습·번들 export·합성번들 | h4s_* · h4d_* · h4r_* · h4_drift_loop_smoke · h4s_export_bundle · gen_synth_bundle |
| [`replay/`](replay/) | 실환자 .psv 궤적을 서빙 /predict 에 재생 | replay_patient · replay_ward |
| [`tools/`](tools/) | 개발 도구 (파이프라인 아님) | mutation_test |

세부는 각 하위폴더의 `README.md` 참고.
