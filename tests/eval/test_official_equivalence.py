"""Hermetic — 우리 eval/utility.py 가 공식 PhysioNet/CinC 2019 스코어러와 bit-equal 인지 검증.

★ 헤드라인 지표(공식 utility score) 방어선. `eval/official_compat.py` 는 공식
`evaluate_sepsis_score.py` 를 verbatim vendoring 하고 `check_equivalence()` 로 우리 구현과
±tol 일치를 비교하는 오라클을 이미 갖고 있지만, **이를 호출하는 테스트가 없어**(커버리지 0%)
등가성 보장이 실행조차 안 되고 있었다. 이 파일이 그 오라클을 엣지케이스 코호트로 돌려,
utility.py 가 공식 공식에서 벗어나면(TP/FN 램프·FP·정규화 상수 등) 회귀로 잡는다.

utility.py 가 공식과 어긋나면 여기서 실패한다 — 그게 목적이다(수치 스큐 = 조용한 잘못된 성능주장).
"""
from __future__ import annotations

import numpy as np

from sepsis.eval.official_compat import check_equivalence

TOL = 1e-6


def _septic(n: int, onset: int) -> np.ndarray:
    """onset 이후 1(SepsisLabel 블록). argmax(labels)=onset 이 되게 구성."""
    lab = np.zeros(n, dtype=int)
    lab[onset:] = 1
    return lab


def _edge_cohort() -> list[tuple[np.ndarray, np.ndarray]]:
    """공식 스코어러의 분기를 두루 밟는 손수 만든 엣지케이스."""
    C: list[tuple[np.ndarray, np.ndarray]] = []
    # 1) 비-패혈증 + 예측 전무 → 전부 TN(u_tn=0)
    C.append((np.zeros(12, int), np.zeros(12, int)))
    # 2) 비-패혈증 + 일부 양성 → FP(u_fp=-0.05)
    p = np.zeros(12, int); p[[2, 5, 9]] = 1
    C.append((np.zeros(12, int), p))
    # 3) 패혈증 + 이른 정확 예측 → TP 램프(m_1) 구간
    lab = _septic(20, 12); p = np.zeros(20, int); p[8:14] = 1
    C.append((lab, p))
    # 4) 패혈증 + optimal~late 창에서만 예측 → m_2 하강 램프
    lab = _septic(20, 10); p = np.zeros(20, int); p[10:16] = 1
    C.append((lab, p))
    # 5) 패혈증 + 완전 놓침 → FN(optimal 이후 m_3 램프, 이전 0)
    lab = _septic(18, 9)
    C.append((lab, np.zeros(18, int)))
    # 6) 패혈증 + 매 스텝 양성 → TP/FP 혼합 + t_sepsis 클리핑
    lab = _septic(15, 3)
    C.append((lab, np.ones(15, int)))
    # 7) 이른 onset(argmax 근처 0) → best_predictions 좌측 클리핑
    lab = _septic(10, 0)
    C.append((lab, np.ones(10, int)))
    # 8) 짧은 시퀀스(n=1, n=2)
    C.append((np.array([0]), np.array([0])))
    C.append((np.array([1]), np.array([1])))
    C.append((_septic(2, 1), np.array([0, 1])))
    return C


def test_utility_matches_official_scorer_on_edge_cases():
    cohort = _edge_cohort()
    ok, max_diff, n_checks, details = check_equivalence(cohort, tol=TOL)
    assert ok, (f"eval/utility.py 가 공식 스코어러에서 벗어남 (max Δ={max_diff:.2e} > {TOL}):\n"
                + "\n".join(details))
    # 환자당 3 검사(observed·best·inaction) + 코호트 정규화 1 → 방어 폭 확인.
    assert n_checks == 3 * len(cohort) + 1
    assert max_diff <= TOL


def test_utility_matches_official_scorer_on_randomized_cohort():
    """넓은 조합(길이·onset·예측 밀도)에서도 bit-equal — 특정 케이스 편향 방지."""
    rng = np.random.default_rng(0)
    cohort: list[tuple[np.ndarray, np.ndarray]] = []
    for _ in range(60):
        n = int(rng.integers(1, 30))
        if rng.random() < 0.5:
            lab = np.zeros(n, int)                      # 비-패혈증
        else:
            lab = _septic(n, int(rng.integers(0, n)))   # 무작위 onset
        preds = (rng.random(n) < 0.4).astype(int)
        cohort.append((lab, preds))

    ok, max_diff, n_checks, details = check_equivalence(cohort, tol=TOL)
    assert ok, (f"무작위 코호트에서 공식과 어긋남 (max Δ={max_diff:.2e}):\n"
                + "\n".join(details[:10]))


def test_check_equivalence_is_not_vacuous():
    """오라클 자체가 진짜로 비교하는지(빈 통과 아님) — 검사 수가 코호트에 비례해야 한다."""
    ok, _, n_checks, _ = check_equivalence(_edge_cohort()[:3], tol=TOL)
    assert ok
    assert n_checks == 3 * 3 + 1   # 3환자 × 3 + 정규화 1
