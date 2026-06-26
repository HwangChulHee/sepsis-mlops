"""Smoke pipeline for sepsis-mlops: end-to-end plumbing check (not performance).

data.py    -> subset selection, feature selection, forward-fill, mean-fill, normalization
dataset.py -> length-8 stride-1 windowing with Method-A (last-timestep) labels
model.py   -> 1-layer GRU
train_smoke.py -> orchestration + MLflow logging + PASS asserts
"""
