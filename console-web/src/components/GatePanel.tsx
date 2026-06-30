/**
 * 게이트 패널 — 수치 크게 + cross-site 정직성 (결정 3, 성공기준 4).
 *   util 3종(bholdout·new·old)을 크게, PASS/REGRESSED 배지는 작게.
 *   cross_site_claim === false 면 "cross-site 일반화 주장 아님" 명시(과대해석 방지).
 */
import type { GateInfo } from "../api";

interface Props {
  gate: GateInfo;
}

const fmt = (n: number | undefined): string =>
  n === undefined || n === null ? "—" : n.toFixed(3);

export default function GatePanel({ gate }: Props) {
  // 3분기: true→PASS / false→REGRESSED / undefined(미완성)→"검증 미완료".
  // (boolean 단순분기는 undefined 를 REGRESSED 로 오표기 — 미완성과 회귀를 뭉갬.)
  const reg = gate.no_regression;
  const regClass = reg === true ? "badge--ok" : reg === false ? "badge--warn" : "badge--muted";
  const regText = reg === true ? "PASS" : reg === false ? "REGRESSED" : "검증 미완료";
  return (
    <div className="gate-panel">
      <div className="gate-panel__utils">
        <span className="gate-panel__util">
          <em>B-holdout</em>
          <strong>{fmt(gate.bholdout_util)}</strong>
        </span>
        <span className="gate-panel__util">
          <em>new A-val</em>
          <strong>{fmt(gate.new_aval_util)}</strong>
        </span>
        <span className="gate-panel__util">
          <em>old A-val</em>
          <strong>{fmt(gate.old_aval_util)}</strong>
        </span>
      </div>
      <span className={`badge ${regClass}`}>{regText}</span>
      {gate.cross_site_claim === false && (
        <p className="gate-panel__cross-site">
          in-distribution 검증 (cross-site 일반화 주장 아님)
        </p>
      )}
    </div>
  );
}
