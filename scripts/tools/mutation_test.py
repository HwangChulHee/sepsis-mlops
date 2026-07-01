"""경량 뮤테이션 테스트 하네스 — "커버리지 100% ≠ 안전"을 실측한다.

핵심 아이디어: 소스에 일부러 버그(변이)를 심고, 해당 테스트가 실패하는지 본다.
  · 테스트 실패 = KILLED = 그 로직을 테스트가 "의미 있게" 검증함(좋음).
  · 테스트 통과 = SURVIVED = 커버리지는 높아도 그 동작을 아무도 검증 안 함(구멍).
커버리지가 "그 줄이 실행됐나"라면, 뮤테이션은 "틀리면 잡나"를 직접 측정한다.

이 파일의 SUITES 는 큐레이션된 변이 명세다 — "테스트가 어떤 버그를 막는지"의 살아있는 문서.
새 로직·테스트를 추가하면 여기에도 대표 변이 몇 개를 넣어 방어력을 회귀로 고정한다.

실행:  uv run python scripts/tools/mutation_test.py
동작:  각 변이마다 원본→변이→scoped 테스트→원본 복구. 생존 변이가 있으면 exit 1.

주의(하네스 위생): PYTHONDONTWRITEBYTECODE=1 로 .pyc 를 안 남긴다. 같은 바이트 길이 변이
(maximum↔minimum, <0↔<1)는 변이/복구가 같은 파일시스템-초에 겹치면 stale .pyc 가 재사용돼
판정이 오염될 수 있어서다(mtime+size 기반 무효화의 함정).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 각 스위트 = {모듈군 이름, 검증 테스트 스코프, 변이 목록[(파일, 원본조각, 변이조각, 설명)]}.
SUITES = [
    {
        "name": "data (누수 방지 대원칙)",
        "tests": ["tests/data/test_leakage_invariants.py"],
        "muts": [
            ("src/sepsis/data/split.py", 'manifest.site == "training_setA"',
             'manifest.site != "training_setA"', "A/B 사이트 뒤집기"),
            ("src/sepsis/data/split.py", "train = a[perm[n_val:]]", "train = a[perm[:]]",
             "train∩val 겹침(환자 누수)"),
            ("src/sepsis/data/split.py", "n_val = int(round(len(a) * val_frac))",
             "n_val = int(len(a) * val_frac)", "round→floor(반올림 의미)"),
            ("src/sepsis/data/split.py", "val = a[perm[:n_val]]", "val = a[perm[:n_val + 1]]",
             "val 크기 off-by-one"),
            ("src/sepsis/data/missing.py", "return np.where(np.isnan(a), mean[None, :], a)",
             "return np.where(~np.isnan(a), mean[None, :], a)", "fill_mean 조건 반전"),
            ("src/sepsis/data/missing.py", "np.maximum.accumulate(row_idx, axis=0, out=row_idx)",
             "np.minimum.accumulate(row_idx, axis=0, out=row_idx)", "ffill 방향 깨기"),
            ("src/sepsis/data/missing.py", "return (~np.isnan(raw)).astype(np.int8)",
             "return (np.isnan(raw)).astype(np.int8)", "missing_mask 극성 반전"),
            ("src/sepsis/data/normalize.py", "std = np.where(std < 1e-8, 1.0, std)",
             "std = np.where(std > 1e-8, 1.0, std)", "상수열 가드 반전"),
            ("src/sepsis/data/normalize.py", "return ((a - mean[None, :]) / std[None, :])",
             "return ((a + mean[None, :]) / std[None, :])", "z-score 부호 오류"),
        ],
    },
    {
        "name": "console/audit (append-only 거버넌스)",
        "tests": ["tests/console/test_audit_append_only.py", "tests/console/test_audit_schema.py"],
        "muts": [
            ("src/sepsis/console/audit.py", "if session.dirty or session.deleted:",
             "if session.dirty:", "flush 가드 DELETE 감지 제거"),
            ("src/sepsis/console/audit.py", "if state.is_update or state.is_delete:",
             "if state.is_update:", "bulk 가드 DELETE 우회 허용"),
        ],
    },
    {
        "name": "drift (스레드안전·표본수)",
        "tests": ["tests/drift/"],
        "muts": [
            ("src/sepsis/drift/run.py", "if summary.shape[0] > n_cal:",
             "if summary.shape[0] < n_cal:", "검출 표본 캡 반전"),
            ("src/sepsis/drift/run.py", "summary = summary[-n_cal:]",
             "summary = summary[:n_cal]", "최근 대신 앞쪽 사용"),
            ("src/sepsis/drift/window.py", "return len(self.patient_ids())",
             "return len(self._snapshot())", "n_patients 가 행수 반환"),
        ],
    },
    {
        "name": "replay (순서·0-fill 금지)",
        "tests": ["tests/replay/"],
        "muts": [
            ("src/sepsis/replay/engine.py", "if speed <= 0:", "if speed < 0:",
             "speed==0 가드 구멍"),
            ("src/sepsis/replay/engine.py", "if i > 0:", "if i > 1:", "sleep off-by-one"),
            ("src/sepsis/replay/orchestrator.py", "pids.count(p) > 1", "pids.count(p) > 2",
             "중복 patient_id 감지 약화"),
            ("src/sepsis/replay/psv_source.py", "None if pd.isna(v) else float(v)",
             "0.0 if pd.isna(v) else float(v)", "결측 NaN→0(0-fill 누수)"),
        ],
    },
    {
        "name": "eval/threshold (τ 선택·평가 코어)",
        "tests": ["tests/eval/test_threshold.py"],
        "muts": [
            ("src/sepsis/eval/threshold.py", "if norm > best_norm:", "if norm < best_norm:",
             "τ 선택이 최소를 고름"),
            ("src/sepsis/eval/threshold.py", "return (u_obs - u_in) / denom",
             "return (u_obs + u_in) / denom", "utility_at 부호 오류"),
            ("src/sepsis/eval/threshold.py",
             "tp.append(U.utility_per_timestep(lab, np.ones(lab.shape[0], np.int8)))",
             "tp.append(U.utility_per_timestep(lab, np.zeros(lab.shape[0], np.int8)))",
             "TP 기여를 무행동으로(정규화 붕괴)"),
        ],
    },
    {
        "name": "serve/preprocess_rt (train-serving skew 가드)",
        "tests": ["tests/serve/test_preprocess_rt.py"],
        "muts": [
            ("src/sepsis/serve/preprocess_rt.py", "obs = ~np.isnan(row)", "obs = np.isnan(row)",
             "관측 마스크 극성 반전"),
            ("src/sepsis/serve/preprocess_rt.py", "state = np.full(self.F, np.nan, dtype=np.float32)",
             "state = np.zeros(self.F, dtype=np.float32)", "leading 상태 0-fill"),
            ("src/sepsis/serve/preprocess_rt.py",
             "a = normalize.clip(a, self.b.clip_lo, self.b.clip_hi)",
             "a = normalize.clip(a, self.b.clip_hi, self.b.clip_lo)", "clip 상/하한 뒤바꿈"),
        ],
    },
    {
        "name": "serve/predictor (환자별 상태·alarm)",
        "tests": ["tests/serve/test_predictor.py"],
        "muts": [
            ("src/sepsis/serve/predictor.py", "bool(p >= self.b.tau)", "bool(p > self.b.tau)",
             "alarm 경계(>=→>)"),
            ("src/sepsis/serve/predictor.py", "self._h[pid] = h_n", "self._h[pid] = h",
             "hidden state 미전진"),
            ("src/sepsis/serve/predictor.py", "torch.sigmoid(logit)", "(logit)",
             "sigmoid 누락(로짓을 확률로)"),
        ],
    },
    {
        "name": "eval/utility (공식 스코어러 등가성)",
        "tests": ["tests/eval/test_official_equivalence.py"],
        "muts": [
            ("src/sepsis/eval/utility.py", "U_FP = -0.05", "U_FP = -0.5",
             "FP 벌점 상수 변조(공식과 어긋남)"),
            ("src/sepsis/eval/utility.py", "MIN_U_FN = -2.0", "MIN_U_FN = -1.0",
             "FN 벌점 상수 변조"),
            ("src/sepsis/eval/utility.py", "return int(np.argmax(labels)) - DT_OPTIMAL",
             "return int(np.argmax(labels)) + DT_OPTIMAL", "onset 오프셋 부호 오류"),
        ],
    },
    {
        "name": "retrain/promote (승격 결정)",
        "tests": ["tests/retrain/test_promote.py"],
        "muts": [
            ("src/sepsis/retrain/promote.py", "if persisted >= persistence:",
             "if persisted > persistence:", "지속성 경계 off-by-one(투자권고 지연)"),
            ("src/sepsis/retrain/promote.py",
             'if d.get("dataset_drift_share", 0.0) > share_threshold:',
             'if d.get("dataset_drift_share", 0.0) >= share_threshold:',
             "임계 strict-greater 약화(경계값 오발동)"),
        ],
    },
]


def run(tests) -> bool:
    """scoped 테스트 전부 통과하면 True(=변이 생존)."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run(
        ["uv", "run", "pytest", *tests, "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    return r.returncode == 0


def main() -> int:
    grand_k = grand_t = 0
    survivors = []
    for suite in SUITES:
        if not run(suite["tests"]):
            print(f"!! baseline 실패(테스트가 원본에서 이미 red): {suite['name']}")
            return 2
        print(f"\n### {suite['name']}")
        k = 0
        for rel, old, new, desc in suite["muts"]:
            p = ROOT / rel
            orig = p.read_text()
            c = orig.count(old)
            if c != 1:
                print(f"  !! 조각 비유일({c}): {old!r} in {rel} — 건너뜀")
                continue
            try:
                p.write_text(orig.replace(old, new))
                survived = run(suite["tests"])
            finally:
                p.write_text(orig)   # 항상 복구
            if survived:
                survivors.append((rel, desc))
                print(f"  SURVIVED ⚠️  {rel.split('/')[-1]:<16}{desc}")
            else:
                k += 1
                print(f"  KILLED ✅   {rel.split('/')[-1]:<16}{desc}")
        n = len(suite["muts"])
        grand_k += k
        grand_t += n
        print(f"  → {k}/{n} 사살")
    print(f"\n총 뮤테이션 점수: {grand_k}/{grand_t} = {100 * grand_k / grand_t:.0f}% 사살")
    if survivors:
        print(f"생존 변이 {len(survivors)}건 — 테스트가 못 잡음:")
        for rel, desc in survivors:
            print(f"  · {rel}: {desc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
