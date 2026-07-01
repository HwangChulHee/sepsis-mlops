"""H1-c — diagnostic EDA (결정 4·7). Reads the H1-a raw cache directly (NaN preserved).

Two diagnostics, both patient-aggregated (so large patients don't dominate):

1. Measurement-density leak check: is a lab MEASURED (non-NaN that hour) more often
   in the positive region (SepsisLabel==1) than the negative region? Plus an
   onset-aligned trend around the first positive (t0). This is the informative-
   missingness / treatment-action leak channel (docs/research/04).

2. Positive-timestep position: where do positive labels sit in the record
   (distance from the record end / relative position) — distribution-bias self-evidence.

Numbers only; interpretation ("is mask-OFF justified") is a human call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sepsis import config as C
from sepsis.data import cache as cache_mod

ONSET_WINDOW = 24  # ± hours around t0 for the onset-aligned trend


@dataclass
class DiagResult:
    n_patients: int
    n_septic: int
    n_onset_at_admission: int
    # diagnostic 1 — per-lab patient-mean measurement rate
    pos_rate: dict[str, float] = field(default_factory=dict)   # septic patients, positive region
    neg_rate: dict[str, float] = field(default_factory=dict)   # septic patients, negative region
    nonseptic_rate: dict[str, float] = field(default_factory=dict)  # non-septic patients (context)
    any_pos_rate: float = 0.0
    any_neg_rate: float = 0.0
    any_nonseptic_rate: float = 0.0
    # onset-aligned (any-lab measured), offset -W..+W
    onset_offsets: list[int] = field(default_factory=list)
    onset_rate: list[float] = field(default_factory=list)
    # diagnostic 2 — positive position
    dist_from_end: list[int] = field(default_factory=list)     # per positive timestep
    rel_position: list[float] = field(default_factory=list)    # idx/(T-1) per positive timestep
    first_pos_rel: list[float] = field(default_factory=list)   # t0/(T-1) per septic patient


def run(cache_dir=None) -> DiagResult:
    manifest = cache_mod.load_manifest(cache_dir)
    lab_idx = [C.CACHE_FEATURES.index(l) for l in C.LABS_9]

    # accumulators
    pos_acc = {l: [] for l in C.LABS_9}
    neg_acc = {l: [] for l in C.LABS_9}
    non_acc = {l: [] for l in C.LABS_9}
    any_pos, any_neg, any_non = [], [], []
    onset_sum = np.zeros(2 * ONSET_WINDOW + 1)
    onset_cnt = np.zeros(2 * ONSET_WINDOW + 1)
    dist_from_end, rel_position, first_pos_rel = [], [], []

    n_septic = 0
    n_onset_adm = 0

    for _, r in manifest.iterrows():
        feats, labels = cache_mod.load_feats_labels(r["site"], r["pid"], cache_dir)
        T = labels.shape[0]
        measured = ~np.isnan(feats[:, lab_idx])      # (T, 9) bool
        any_measured = measured.any(axis=1)          # (T,)
        pos = labels == 1
        neg = labels == 0

        if pos.any():  # septic patient
            n_septic += 1
            for k, l in enumerate(C.LABS_9):
                pos_acc[l].append(measured[pos, k].mean())
                if neg.any():
                    neg_acc[l].append(measured[neg, k].mean())
            any_pos.append(any_measured[pos].mean())
            if neg.any():
                any_neg.append(any_measured[neg].mean())

            # diagnostic 2 — positive positions
            pidx = np.flatnonzero(pos)
            t0 = int(pidx[0])
            if t0 == 0:
                n_onset_adm += 1
            for i in pidx:
                dist_from_end.append(int(T - 1 - i))
                if T > 1:
                    rel_position.append(i / (T - 1))
            if T > 1:
                first_pos_rel.append(t0 / (T - 1))

            # onset-aligned any-lab measurement rate
            for off in range(-ONSET_WINDOW, ONSET_WINDOW + 1):
                t = t0 + off
                if 0 <= t < T:
                    j = off + ONSET_WINDOW
                    onset_sum[j] += any_measured[t]
                    onset_cnt[j] += 1
        else:  # non-septic patient (context)
            for k, l in enumerate(C.LABS_9):
                non_acc[l].append(measured[:, k].mean())
            any_non.append(any_measured.mean())

    def m(d):
        return {l: float(np.mean(v)) if v else float("nan") for l, v in d.items()}

    offsets = list(range(-ONSET_WINDOW, ONSET_WINDOW + 1))
    onset_rate = [float(onset_sum[i] / onset_cnt[i]) if onset_cnt[i] else float("nan")
                  for i in range(len(offsets))]

    return DiagResult(
        n_patients=len(manifest),
        n_septic=n_septic,
        n_onset_at_admission=n_onset_adm,
        pos_rate=m(pos_acc), neg_rate=m(neg_acc), nonseptic_rate=m(non_acc),
        any_pos_rate=float(np.mean(any_pos)) if any_pos else float("nan"),
        any_neg_rate=float(np.mean(any_neg)) if any_neg else float("nan"),
        any_nonseptic_rate=float(np.mean(any_non)) if any_non else float("nan"),
        onset_offsets=offsets, onset_rate=onset_rate,
        dist_from_end=dist_from_end, rel_position=rel_position, first_pos_rel=first_pos_rel,
    )
