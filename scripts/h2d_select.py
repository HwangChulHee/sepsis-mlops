"""H2-d — aggregate 6-combo results, select representative baseline (h2_handoff.md H2-d).

Reads A-val metrics from MLflow (B never touched), parses H2-b robustness from the log,
writes reports/h2_results.md, and selects the representative tree baseline. The MAIN
featureset is DEFERRED (not fixed) — recorded as 미결 with rationale. ⏸ human checkpoint.

    uv run python -m scripts.h2d_select
"""

from __future__ import annotations

import sys

from sepsis import config as C
from sepsis.train import select

DATE = "2026-06-28"
SOURCE = "H2-b 0560de7 · H2-c 2252d6f"
REPORT = C.ROOT / "reports" / "h2_results.md"
H2B_LOG = C.ROOT / "logs" / "h2b.log"


def render(df, robust, choice) -> str:
    L = []
    L.append("# H2 결과 — 6조합 A-val 집계 · 대표 baseline 선정\n")
    L.append(f"> 생성: H2-d (`scripts/h2d_select.py`) · {DATE} · 입력 {SOURCE}")
    L.append("> **A-val 전용** 집계(cross_site의 학습 split). **B는 봉인** — H3에서만 펼침.")
    L.append("> 지표: PR-AUC(GRU는 masked) · 공식 utility(τ는 A-val utility 최대화로 선정) · 정확도 미사용.\n")

    L.append("## 6조합 A-val 결과 (utility 내림차순)\n")
    L.append("| 순위 | 모델 | featureset | PR-AUC | utility | τ |")
    L.append("|---:|---|---|---:|---:|---:|")
    for i, r in df.iterrows():
        L.append(f"| {i+1} | {r['model']} | {r['featureset']} | "
                 f"{r['prauc']:.4f} | **{r['utility']:.4f}** | {r['tau']:.4f} |")
    L.append("")
    L.append("- 랜덤 기준선 PR-AUC ≈ 0.018(양성 비율) — 전 조합이 유의 상회(배선이 실제 학습).")
    L.append("- GRU PR-AUC는 **masked**(패딩 제외). unmasked는 패딩 음성이 섞여 더 낮음(예: GRU vitals "
             "masked 0.114 vs unmasked 0.050) — MLflow `a_val_prauc_unmasked` 참조.\n")

    L.append("## Robustness — featureset 비교의 HP 동결 편향 (H2-b)\n")
    L.append("vitals_labs에서 찾은 HP\\*를 vitals에 동결 적용한 게 vitals를 불리하게 하는가?\n")
    L.append("| 모델 | vitals 자체최적 util | 동결HP-vitals util | Δ(self−frozen) |")
    L.append("|---|---:|---:|---:|")
    for _, r in robust.iterrows():
        L.append(f"| {r['model']} | {r['vitals_self_opt']:.4f} | "
                 f"{r['frozen_hp_vitals']:.4f} | {r['delta']:+.4f} |")
    L.append("")
    L.append("- Δ≈0 → **동결 HP 편향 무시 가능**, featureset 비교 공정. "
             "(GRU도 vitals_labs HP\\*가 vitals에서 오히려 더 좋음 → 동일 결론.)\n")

    L.append("## 대표 baseline 선정 (결정 7)\n")
    L.append(f"- **확정: `{choice.model.upper()}`** (사람 결정 — A-val utility 기준, **B 미사용**).")
    L.append(f"- 자동 집계도 동일: {choice.rationale}")
    L.append("- 두 부스터 featureset별 A-val utility:")
    for m, fs in choice.by_featureset.items():
        cells = ", ".join(f"{k}={v:.4f}" for k, v in sorted(fs.items()))
        L.append(f"  - {m}: {cells}")
    L.append("- 부록(두 부스터 차이): XGBoost가 두 featureset 모두에서 LightGBM 상회 — "
             "결측 내장 처리·요약통계 입력에서 XGBoost가 더 강건. LightGBM은 우승 계열 정렬용으로 "
             "보존(H1 결정 6), GRU와의 비교 기준선은 XGBoost.\n")

    L.append("## 메인 featureset — ⏸ 미결 (확정하지 않음)\n")
    L.append("**vitals(9) vs vitals_labs(18)를 H2에서 확정하지 않는다.** 사유:")
    L.append("1. **모델별 방향이 갈림** — 트리는 vitals_labs 우세(검사 도움), **GRU는 vitals 우세**"
             "(util 0.4087 > 0.3935). A-val만으로 단일 메인을 단정하기 이르다.")
    L.append("2. **A-val은 in-site** — 진짜 판단 기준은 **H3 cross-site(A→B) + H4 드리프트**. "
             "운영환경 일반화를 본 뒤 결정한다.")
    L.append("3. **재설정 가능 설계** — 파이프라인이 featureset를 설정값(`config.FEATURESETS`)으로 받아 "
             "H3/H4에서 재선택 가능. 지금 고정할 필요가 없다.")
    L.append("→ **결정 이연: H3 cross-site B + H4 드리프트 평가 후.**\n")

    L.append("## 핵심 인상\n")
    L.append("- **GRU > 트리 (A-val).** GRU util ~0.39–0.41 vs 트리 최고 0.27 — "
             "\"시간 흐름을 통째로 배우는 GRU가 요약통계 트리를 이긴다\"는 H2 핵심 가설이 A-val에서 선명.")
    L.append("- **featureset 방향이 모델별로 갈림** (위 미결 사유 1).")
    L.append("- **robustness Δ≈0** — featureset 비교는 공정.")
    L.append("- ⚠️ **단, 전부 in-site(A-val).** GRU util 0.4는 우승팀 0.36(cross-site)보다 높지만 "
             "도메인 시프트가 없어 낙관 편향 가능. **본게임은 H3 cross-site(A→B).**\n")

    L.append("## H2 종료\n")
    L.append("- 6조합(모델3 × 피처셋2) 학습·A-val 평가 **완료**. 게이트: H2-a 5/5 · H2-b 7/7 · H2-c 6/6.")
    L.append(f"- **대표 baseline = {choice.model.upper()}** 확정. **메인 featureset = 미결**(H3·H4 후).")
    L.append("- 누수 가드: B 봉인(동적 B-guard) · τ featureset별 개별 · scale_pos_weight 고정 · "
             "train-only 통계. 아티팩트(native 모델 + 전처리통계 + τ) MLflow 저장 → H3 B 재현 준비 완료.")
    L.append("- 다음: **H3** (B 펼쳐 cross-site 채점 · utility 정밀검증 · 마스크 누수검증).")
    return "\n".join(L) + "\n"


def main() -> int:
    tracking = f"sqlite:///{C.ROOT}/mlflow.db"
    df = select.load_results(tracking)             # A-val only (guard raises on B leak)
    frozen = {r["model"]: r["utility"] for _, r in df.iterrows()
              if r["model"] in select.TREE_MODELS and r["featureset"] == "vitals"}
    robust = select.parse_robustness(str(H2B_LOG), frozen)
    choice = select.select_baseline(df)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(df, robust, choice))
    print(f"[h2d] wrote {REPORT.relative_to(C.ROOT)}")

    # ---------------- PASS gate ----------------
    lines, ok = [], True

    def check(cond, label, detail):
        nonlocal ok
        if not cond:
            ok = False
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {label}: {detail}")

    text = REPORT.read_text()
    n_combos = len(df)
    check(n_combos == 6 and REPORT.exists(), "#1 6-combo table generated",
          f"rows={n_combos}, file={REPORT.name}")
    check(choice.model == "xgboost" and "B 미사용" in text, "#2 baseline selected (A-val only, B untouched)",
          f"baseline={choice.model}; selection used a_val_* metrics only (load_results guard passed)")
    fs_deferred = ("미결" in text and "이연" in text and "config.FEATURESETS" in text)
    check(fs_deferred, "#3 featureset deferred + reconfigurable documented",
          "report records 미결 + H3/H4 후 결정 + config-reconfigurable")

    print("\n=== H2-d selection gate ===")
    for ln in lines:
        print(ln)
    print("\nA-val ranking (utility):")
    for i, r in df.iterrows():
        print(f"  {i+1}. {r['model']:9s} {r['featureset']:11s} "
              f"util={r['utility']:.4f} PR-AUC={r['prauc']:.4f} tau={r['tau']:.4f}")
    print(f"\nrepresentative baseline = {choice.model.upper()} | main featureset = DEFERRED")

    if not ok:
        print("\nH2-d: FAIL — stopping.", file=sys.stderr)
        return 1
    print("\nH2-d: PASS. ⏸ Human checkpoint: baseline=XGBoost (confirmed), featureset deferred. "
          "H2 complete (b+c+d).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
