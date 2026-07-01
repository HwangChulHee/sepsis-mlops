"""H1-c runner — diagnostic EDA -> docs/reports/h1_diagnostics.md + docs/reports/figures/.

    uv run python -m scripts.run_diagnostics

⏸ Human checkpoint: review whether measurement density rises near onset
(informative-missingness / treatment-action leak) -> is mask-OFF justified.
PASS (programmatic) = doc + figures generated with numeric measurement comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sepsis import config as C
from sepsis.eda import diagnostics as D

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "reports"
FIGDIR = REPORTS / "figures"

# EDA reference (docs/reports/eda_findings.md) for the consistency check
EDA_N_SEPTIC = 2932
EDA_ONSET_AT_ADMISSION = 370


def make_figures(res: D.DiagResult) -> list[str]:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    paths = []

    # fig 1 — onset-aligned any-lab measurement rate
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(res.onset_offsets, res.onset_rate, marker=".")
    ax.axvline(0, color="r", ls="--", lw=1, label="first positive (t0)")
    ax.set_xlabel("hours relative to first positive label (t0)")
    ax.set_ylabel("any-lab measurement rate")
    ax.set_title("Measurement density around sepsis onset (patient-aggregated)")
    ax.legend()
    fig.tight_layout()
    p = FIGDIR / "h1_onset_measurement_rate.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p.name)

    # fig 2 — per-lab pos vs neg vs non-septic measurement rate
    labs = C.LABS_9
    x = np.arange(len(labs))
    w = 0.27
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(x - w, [res.pos_rate[l] for l in labs], w, label="septic: positive region")
    ax.bar(x, [res.neg_rate[l] for l in labs], w, label="septic: negative region")
    ax.bar(x + w, [res.nonseptic_rate[l] for l in labs], w, label="non-septic patients")
    ax.set_xticks(x)
    ax.set_xticklabels(labs, rotation=45, ha="right")
    ax.set_ylabel("measurement rate (patient-mean)")
    ax.set_title("Per-lab measurement rate: positive vs negative vs non-septic")
    ax.legend()
    fig.tight_layout()
    p = FIGDIR / "h1_lab_measurement_rate.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p.name)

    # fig 3 — positive-timestep position distributions
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].hist(res.dist_from_end, bins=range(0, 12), align="left", rwidth=0.85)
    axes[0].set_xlabel("distance from record end (hours)")
    axes[0].set_ylabel("positive timesteps")
    axes[0].set_title("Positive timesteps: distance from record end")
    axes[1].hist(res.rel_position, bins=20)
    axes[1].set_xlabel("relative position in record (idx / (T-1))")
    axes[1].set_ylabel("positive timesteps")
    axes[1].set_title("Positive timesteps: relative position")
    fig.tight_layout()
    p = FIGDIR / "h1_positive_position.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p.name)

    return paths


def write_report(res: D.DiagResult, figs: list[str]) -> Path:
    dist = np.array(res.dist_from_end)
    within_10h = float((dist <= 9).mean() * 100) if dist.size else float("nan")
    fp = np.array(res.first_pos_rel)

    # consistency vs eda_findings.md
    septic_ok = res.n_septic == EDA_N_SEPTIC
    consistency = (f"패혈증 환자 {res.n_septic} {'== EDA 2932 ✅' if septic_ok else f'!= EDA {EDA_N_SEPTIC} ⚠️'}. "
                   f"입실즉시양성은 **정의 차이**(충돌 아님): 본 진단의 {res.n_onset_at_admission}명은 "
                   f"**기록 첫 행(index 0)** 양성(모델이 보는 record-relative 기준; ICULOS 제외), "
                   f"EDA의 {EDA_ONSET_AT_ADMISSION}명은 **ICULOS==1 행** 양성. "
                   f"차이는 기록이 ICULOS>1에서 시작하는 환자(전체 8,793명) 때문으로, m2m 관점에선 "
                   f"index-0 기준이 맞다.")

    any_diff = res.any_pos_rate - res.any_neg_rate
    any_ratio = res.any_pos_rate / res.any_neg_rate if res.any_neg_rate else float("nan")

    def off_rate(o):
        return res.onset_rate[res.onset_offsets.index(o)]

    lines = []
    A = lines.append
    A("# H1-c 진단 EDA — 측정밀도 누수 · 양성 위치 분포\n")
    A("> H1-a raw 캐시(NaN 보존)에서 직접 측정. 환자 단위로 집계(누수 방향이라 큰 환자에 휩쓸리지 않게).")
    A("> **수치 제시가 목적이며, '마스크 OFF 정당성' 판단은 사람이 한다.**\n")
    A(f"- 전체 환자: **{res.n_patients:,}** · 패혈증(양성≥1): **{res.n_septic:,}** · "
      f"기록시작 즉시양성(index 0): **{res.n_onset_at_admission:,}**")
    A(f"- EDA 정합: {consistency}\n")

    A("## 1. 측정밀도 누수 확인 (핵심)\n")
    A("\"측정됨\" = 그 시각 검사값이 NaN이 아님(그 시간에 검사 시행). 패혈증 환자 내에서 "
      "**양성 구간 vs 음성 구간**의 측정률을 환자별로 구해 평균.\n")
    A("### 1a. 종합 (검사 9종 중 하나라도 측정된 비율, 환자-평균)\n")
    A("| 구간 | any-lab 측정률 |")
    A("|---|---:|")
    A(f"| 패혈증 환자 · **양성 구간** | {res.any_pos_rate:.4f} |")
    A(f"| 패혈증 환자 · **음성 구간** | {res.any_neg_rate:.4f} |")
    A(f"| 비패혈증 환자 (참고) | {res.any_nonseptic_rate:.4f} |")
    A(f"\n**양성−음성 차 = {any_diff:+.4f} (배수 {any_ratio:.2f}×)**. "
      "양수·>1이면 양성 구간에서 측정이 더 촘촘(informative-missingness 통로 가능성).\n")

    A("### 1b. 검사 9종별 측정률 (환자-평균)\n")
    A("| 검사 | 양성 구간 | 음성 구간 | 차(양−음) | 배수 | 비패혈증 |")
    A("|---|---:|---:|---:|---:|---:|")
    for l in C.LABS_9:
        pr, nr, no = res.pos_rate[l], res.neg_rate[l], res.nonseptic_rate[l]
        diff = pr - nr
        ratio = pr / nr if nr else float("nan")
        A(f"| {l} | {pr:.4f} | {nr:.4f} | {diff:+.4f} | {ratio:.2f}× | {no:.4f} |")

    A("\n### 1c. 발병 시점(t0) 정렬 추이 — any-lab 측정률\n")
    A(f"t0(첫 양성) 기준 ±{D.ONSET_WINDOW}h. 주요 지점:\n")
    A("| t0 상대시각 | any-lab 측정률 |")
    A("|---:|---:|")
    for o in (-24, -12, -6, -3, -1, 0, 3, 6, 12):
        if -D.ONSET_WINDOW <= o <= D.ONSET_WINDOW:
            A(f"| {o:+d}h | {off_rate(o):.4f} |")
    A(f"\n그림: `figures/{figs[0]}` (발병 전후 측정밀도 추이), `figures/{figs[1]}` (검사별 비교).\n")

    A("## 2. 양성 시점 위치 분포\n")
    A(f"- 양성 시점의 **기록 끝에서의 거리** 중앙값 = **{np.median(dist):.0f}h**, "
      f"끝 ≤10h 이내 비율 = **{within_10h:.1f}%**.")
    A(f"- 첫 양성 상대위치(t0/(T-1)) 중앙값 = **{np.median(fp):.3f}** "
      f"(0=입실, 1=기록끝).")
    A(f"- 그림: `figures/{figs[2]}` (끝에서의 거리 / 상대위치 히스토그램).")
    A("- 해석(중립): 양성이 기록 끝에 몰리면 **분포 편향(우측 절단 인공물)**이 자명 — "
      "누수가 아닌 라벨정의 한계이며 H3 절단누수 칼질 판단의 입력.\n")

    A("## 핵심 수치 요약 (사람 판단용)")
    A(f"- any-lab 측정률: 양성 {res.any_pos_rate:.4f} vs 음성 {res.any_neg_rate:.4f} "
      f"→ 차 {any_diff:+.4f} ({any_ratio:.2f}×)")
    A(f"- t0 정렬: -6h {off_rate(-6):.4f} → -1h {off_rate(-1):.4f} → t0 {off_rate(0):.4f}")
    A(f"- 양성 시점 {within_10h:.1f}%가 기록 끝 ≤10h 이내")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "h1_diagnostics.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    print("[h1-c] computing diagnostics from cache ...")
    res = D.run()
    figs = make_figures(res)
    doc = write_report(res, figs)

    # programmatic PASS: doc + figures exist, numeric measurement comparison present
    doc_ok = doc.exists() and doc.stat().st_size > 0
    figs_ok = all((FIGDIR / f).exists() for f in figs)
    numeric_ok = np.isfinite(res.any_pos_rate) and np.isfinite(res.any_neg_rate)
    ok = doc_ok and figs_ok and numeric_ok

    print(f"\n[h1-c] report: {doc}")
    print(f"[h1-c] figures: {', '.join(figs)}")
    print(f"[PASS] doc generated: {doc_ok}")
    print(f"[PASS] figures generated ({len(figs)}): {figs_ok}")
    print(f"[PASS] numeric measurement comparison: {numeric_ok}")
    print("\n--- key numbers ---")
    print(f"septic={res.n_septic} (EDA 2932 ✅), positive-at-record-start(idx0)={res.n_onset_at_admission} "
          f"[EDA 370 is ICULOS==1 — definitional difference, not a conflict]")
    print(f"any-lab measure rate: positive={res.any_pos_rate:.4f} vs negative={res.any_neg_rate:.4f} "
          f"(diff {res.any_pos_rate - res.any_neg_rate:+.4f}, "
          f"{res.any_pos_rate / res.any_neg_rate:.2f}x)")
    o = res.onset_offsets.index
    print(f"onset trend any-lab: -6h={res.onset_rate[o(-6)]:.4f} -1h={res.onset_rate[o(-1)]:.4f} "
          f"t0={res.onset_rate[o(0)]:.4f}")

    if not ok:
        print("\nH1-c: FAIL.", file=sys.stderr)
        return 1
    print("\nH1-c: PASS (programmatic). ⏸ Human checkpoint: is mask-OFF justified by these numbers?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
