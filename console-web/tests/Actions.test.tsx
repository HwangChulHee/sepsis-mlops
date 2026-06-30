/**
 * 승인/롤백 버튼 게이팅 (성공기준 2·2b)
 * 권위: handoff_frontend.md 프론트고유계약 1(승인: gate_passed!==true || !ready → disabled, M3 이중게이트)
 *       + 계약 7 / 성공기준 2b(롤백: bucket==="archived" 에만 활성, BR2-1 — 1차 방어선)
 *       src/sepsis/console/service.py: incomplete면 gate_passed=null(214), 롤백 무게이트(103-112)
 *
 * 출제자 정의 모듈 계약: console-web/src/components/Actions.tsx default export.
 *   props: { bucket: string; gatePassed: boolean|null; ready: boolean;
 *            onApprove?: ()=>void; onRollback?: ()=>void }
 *   승인 버튼 accessible name=/승인/, 롤백 버튼=/롤백/.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import Actions from "../src/components/Actions";

const approveBtn = () => screen.getByRole("button", { name: /승인/ });
const rollbackBtn = () => screen.getByRole("button", { name: /롤백/ });

describe("승인 버튼 게이팅 — gate_passed!==true || !ready (성공기준 2, mn1)", () => {
  it("gate_passed=true & ready=true → 승인 활성", () => {
    render(<Actions bucket="challenger" gatePassed={true} ready={true} />);
    expect(approveBtn()).toBeEnabled();
  });

  it("gate_passed=false(REGRESSED) → 승인 disabled", () => {
    render(<Actions bucket="challenger" gatePassed={false} ready={true} />);
    expect(approveBtn()).toBeDisabled();
  });

  it("gate_passed=null(incomplete) → 승인 disabled (=== false 만 보면 헛클릭, mn1)", () => {
    render(<Actions bucket="incomplete" gatePassed={null} ready={false} />);
    expect(approveBtn()).toBeDisabled();
  });

  it("ready=false(미완성) → gate_passed=true여도 승인 disabled", () => {
    render(<Actions bucket="challenger" gatePassed={true} ready={false} />);
    expect(approveBtn()).toBeDisabled();
  });
});

describe("롤백 버튼 게이팅 — bucket==='archived' 한정 (성공기준 2b, BR2-1)", () => {
  it("bucket='archived'(과거활성) → 롤백 활성", () => {
    render(<Actions bucket="archived" gatePassed={true} ready={true} />);
    expect(rollbackBtn()).toBeEnabled();
  });

  it("bucket='champion'(현재활성) → 롤백 disabled", () => {
    render(<Actions bucket="champion" gatePassed={true} ready={true} />);
    expect(rollbackBtn()).toBeDisabled();
  });

  it("bucket='challenger' → 롤백 disabled (무게이트 alias 교체 차단)", () => {
    render(<Actions bucket="challenger" gatePassed={false} ready={true} />);
    expect(rollbackBtn()).toBeDisabled();
  });

  it("bucket='incomplete' → 롤백 disabled", () => {
    render(<Actions bucket="incomplete" gatePassed={null} ready={false} />);
    expect(rollbackBtn()).toBeDisabled();
  });
});
