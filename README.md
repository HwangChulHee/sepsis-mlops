# sepsis-mlops

Reusable MLOps skeleton applied to **patient time-series → real-time sepsis early
warning**, using the PhysioNet/CinC 2019 Challenge data. Third in a series of
domain-transfer MLOps repos (after `pdm-mlops` and `chest-xray-mlops`).

This stage (step 0) is **EDA only** — measure the raw data to justify later
smoke-pipeline design decisions (window size, missingness handling, split
strategy, class imbalance). No modeling, no preprocessing, no infra yet.

## Data

- **Source**: PhysioNet/CinC Challenge 2019 — *Early Prediction of Sepsis from
  Clinical Data*. Public, no credentialing required.
- **Layout**: one `.psv` file (pipe-separated) per patient; one row = one ICU hour;
  41 columns = 40 features + `SepsisLabel`.
  - `data/raw/training_setA/` — 20,336 patients (hospital A)
  - `data/raw/training_setB/` — 20,000 patients (hospital B)
- **Not committed** — `data/` is git-ignored. Reproduce with:

  ```bash
  bash scripts/download_data.sh    # ~315 MB, pulls all 40,336 files from the PhysioNet S3 mirror
  ```

## EDA

```bash
uv sync
uv run python scripts/eda.py
```

Outputs:
- console report
- `reports/eda_findings.md` — findings with numbers + design implications
- `reports/figures/*.png` — sequence-length, first-positive-label, and vital-sign histograms

See **[reports/eda_findings.md](reports/eda_findings.md)** for the writeup. Headline numbers:

| | |
|---|---|
| Patients (A / B / total) | 20,336 / 20,000 / 40,336 |
| Patient-hours (rows) | 1,552,210 |
| Sepsis patients (≥1 positive hour) | 7.27% |
| Positive patient-hours | 1.80% (neg:pos ≈ 55) |
| Median ICU length | 38 h |
| Positive-label window | contiguous, ≤10 h, ends at record end (100% of cases) |
| Worst missingness | labs >90% NaN; vitals 10–66% NaN |

## Environment

WSL2 Ubuntu · `uv` + `pyproject.toml` · `pandas`, `numpy`, `matplotlib`.
