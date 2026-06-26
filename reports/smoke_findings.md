# Smoke Pipeline Findings — sepsis-mlops (Handoff 02)

> **Goal: structural verification, not performance.** Confirm the training loop runs
> end-to-end (data → windowing → train → eval → MLflow) with no plumbing leaks, on a
> ~1,000-patient CPU subset. Metric *values* are meaningless here; only wiring matters.

Run it:

```bash
uv sync
uv run python -m smoke.train_smoke           # defaults: 1000 patients, 2 epochs, CPU
```

Modules: `smoke/data.py` (subset, features, ffill/mean-fill, normalization, split) ·
`smoke/dataset.py` (windowing + Method-A labels) · `smoke/model.py` (1-layer GRU) ·
`smoke/train_smoke.py` (orchestration + MLflow + asserts).

## Result: **SMOKE PASS**

Recorded run (seed 42): `run_id = 67ab95ad1fda477597d29c8f017f5a35`, experiment `sepsis-smoke`.

| # | PASS criterion | Result |
|---|---|---|
| 1 | ~1,000 patients, both sites loaded | 1000 patients; sites `{training_setA, training_setB}` ✅ |
| 2 | windows > 0, **positive windows > 0** | train 23,436 / val 6,691 windows; **positive: train 386, val 49** ✅ |
| 3 | patient-level split, **intersection 0** | train 800 (A398/B402), val 200 (A100/B100); intersection **0** ✅ |
| 4 | **NaN 0** right before model | `assert not np.isnan(X).any()` passes for train+val ✅ |
| 5 | ≥1 epoch, no error (CPU) | 2 epochs completed ✅ |
| 6 | train loss **finite** | epoch1 1.2724 → epoch2 1.1819 (finite) ✅ |
| 7 | val PR-AUC computed + logged | epoch1 0.1127 → epoch2 0.2028 (value meaningless) ✅ |
| 8 | MLflow run + params + metrics + **model artifact** | params(11) + metrics + `MLmodel`/`data/model.pth` logged; reloaded via `mlflow.pytorch.load_model` ✅ |
| 9 | single-window inference → prob in [0,1] | 0.1578 ✅ |

### Wiring confirmed (the things that actually had to be right)
- **No patient leakage**: split is per-file (= per-patient), site-aware; train/val patient
  id intersection asserted 0.
- **No standardization leakage**: z-score mean/std computed on **train patients only**,
  applied to val. Same for the mean used to fill still-missing columns.
- **No NaN propagation**: per-patient forward-fill → train-mean fill → `assert` 0 NaNs.
  (zero-fill avoided — 0 is a real measurement.)
- **Label alignment (off-by-one)**: each length-8 window's target is the `SepsisLabel`
  at the window's **last** hour. Verified by construction in `make_windows`.
- **Imbalance handling**: `pos_weight = neg/pos windows (train) = 59.72` — consistent with
  the EDA's full-data ≈55. Passed to `BCEWithLogitsLoss` as a scalar tensor.

## ⚠️ Known leak carried forward (acknowledged, NOT fixed — out of scope)

**Record-truncation leak.** From the EDA (`reports/eda_findings.md` §6): for septic
patients the record is **right-truncated near onset** — the positive label is a contiguous
block of **≤10 hours that always ends at the last recorded hour** (100% of sepsis
patients). The smoke uses these labels as-is (Method A), so a model could learn the
artifact *"the record is about to end"* as a positive signal rather than genuine
physiology. The window labeling here makes positive windows cluster at the end of septic
records.

This is **deliberately not addressed in the smoke** (the goal is plumbing). It is the
**top labeling task for full training**, candidate mitigations:
- drop the last *k* hours of each septic record, or exclude windows whose end is within
  *k* hours of record end;
- prediction-horizon labeling (predict sepsis *m* hours ahead) instead of same-hour labels;
- compare against a non-truncated cohort definition.

## Notes / deviations
- **Features**: 10 = 8 vitals (`HR,O2Sat,Temp,SBP,MAP,DBP,Resp,EtCO2`) + `Age,Gender`.
  Labs / `Unit1,Unit2,HospAdmTime,ICULOS` excluded for the smoke (per Handoff 02 §3.2).
- **MLflow store**: local file store under `mlruns/`. mlflow 3.x gates the file backend
  behind `MLFLOW_ALLOW_FILE_STORE=true` (set in-process) and defaults model serialization
  to `pt2`; `pt2` traces the GRU via `torch.export` and fails on a batch-dim-1 example, so
  the model is logged with `serialization_format="pickle"`. `mlruns/` is git-ignored
  (regenerable by re-running the script) — the run is documented here, not committed.
- **Out of scope (untouched)**: full/GPU training, tuning, truncation-leak fix, missingness
  masks/labs, Method B, test split, serving infra, model-registry registration.
