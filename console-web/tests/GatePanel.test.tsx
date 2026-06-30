/**
 * 게이트 패널 cross-site 정직성 (성공기준 4)
 * 권위: handoff_frontend.md 프론트고유계약 4(cross_site_claim=false → "cross-site 일반화 주장 아님")
 *       src/sepsis/console/service.py get_version_detail.gate = validation.json 통째(cross_site_claim 포함)
 *
 * 출제자 정의 모듈 계약: console-web/src/components/GatePanel.tsx default export.
 *   props: { gate: { cross_site_claim?: boolean; bholdout_util?: number;
 *                     new_aval_util?: number; old_aval_util?: number; no_regression?: boolean } }
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import GatePanel from "../src/components/GatePanel";

const gate = {
  no_regression: true,
  bholdout_util: 0.30,
  new_aval_util: 0.28,
  old_aval_util: 0.25,
  cross_site_claim: false,
};

describe("GatePanel cross_site_claim 정직성 (성공기준 4)", () => {
  it("cross_site_claim=false → 'cross-site 주장 아님' 명시", () => {
    render(<GatePanel gate={gate} />);
    // handoff: "in-distribution 검증 (cross-site 일반화 주장 아님)"
    expect(screen.getByText(/주장 아님/)).toBeInTheDocument();
  });

  it("cross_site_claim=true → '주장 아님' 부인을 표시하지 않는다", () => {
    render(<GatePanel gate={{ ...gate, cross_site_claim: true }} />);
    expect(screen.queryByText(/주장 아님/)).not.toBeInTheDocument();
  });
});
