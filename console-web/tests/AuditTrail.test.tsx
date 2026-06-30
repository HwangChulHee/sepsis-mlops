/**
 * AuditTrail 클라측 버전 필터 (mn2)
 * 권위: handoff_frontend.md AuditTrail("이 버전 관련"은 클라가 from/to_version === 디렉토리명 으로 거름)
 *       서버 /console/audit 은 fs 필터만 — 버전 단위 필터 없음(api.py:46).
 *
 * 계약: getAudit({fs}) 결과에서 from_version===dirName || to_version===dirName 인 행만 렌더.
 *       비교 키는 디렉토리명(toDirName(fs, version)) — stripped 직접 비교 금지(B1 정합).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const getAudit = vi.fn();
vi.mock("../src/api", () => ({
  getAudit: (...a: unknown[]) => getAudit(...a),
  toDirName: (fs: string, v: string) => (v.startsWith(`gru_${fs}@`) ? v : `gru_${fs}@${v}`),
}));

import AuditTrail from "../src/components/AuditTrail";

const ev = (over: Partial<Record<string, unknown>>) => ({
  id: 0,
  ts: "2026-06-01T00:00:00Z",
  event_type: "approve",
  featureset: "vitals",
  gate_passed: true,
  from_version: null,
  to_version: null,
  run_id: null,
  git_commit: null,
  actor_unverified: "alice",
  verified_subject: null,
  reason: "ship",
  ...over,
});

beforeEach(() => getAudit.mockReset());

describe("AuditTrail 클라측 버전 필터 (mn2)", () => {
  it("from_version 또는 to_version 이 디렉토리명과 일치하는 행만 표시한다", async () => {
    getAudit.mockResolvedValue([
      ev({ id: 1, to_version: "gru_vitals@chal1", reason: "이-버전-to" }),
      ev({ id: 2, from_version: "gru_vitals@chal1", reason: "이-버전-from" }),
      ev({ id: 3, to_version: "gru_vitals@other", reason: "다른-버전" }),
    ]);

    render(<AuditTrail featureset="vitals" version="chal1" />);

    expect(await screen.findByText("이-버전-to")).toBeInTheDocument();
    expect(screen.getByText("이-버전-from")).toBeInTheDocument();
    expect(screen.queryByText("다른-버전")).not.toBeInTheDocument();
  });

  it("stripped 버전을 디렉토리명으로 맞춰 비교한다(stripped 직접 비교 아님)", async () => {
    // 행은 디렉토리명만 들고 있고, 컴포넌트엔 stripped 'chal1' 를 넘긴다.
    getAudit.mockResolvedValue([ev({ id: 1, to_version: "gru_vitals@chal1", reason: "매칭됨" })]);
    render(<AuditTrail featureset="vitals" version="chal1" />);
    expect(await screen.findByText("매칭됨")).toBeInTheDocument();
  });

  it("관련 행이 없으면 빈 안내를 표시한다", async () => {
    getAudit.mockResolvedValue([ev({ id: 3, to_version: "gru_vitals@other" })]);
    render(<AuditTrail featureset="vitals" version="chal1" />);
    expect(await screen.findByText(/감사 기록 없음/)).toBeInTheDocument();
  });
});
