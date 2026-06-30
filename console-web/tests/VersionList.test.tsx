/**
 * 버킷 정렬 + archived 빔 안내 (성공기준 1·4)
 * 권위: handoff_frontend.md 컴포넌트구조(VersionList: champion→challenger→archived→incomplete)
 *       + 프론트고유계약 3(archived 빔 "거짓 복원 안 함")
 *       src/sepsis/console/service.py list_versions: versions[].{version,bucket,ready,gate_passed,bholdout_util,has_mlflow}
 *
 * 출제자 정의 모듈 계약: console-web/src/components/VersionList.tsx default export.
 *   props: { versions: Array<{version,bucket,ready,gate_passed,bholdout_util,has_mlflow}> }
 * 각 행은 data-testid="version-row" + data-bucket={bucket} 를 노출한다(정렬 검증 훅).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
// RED: 구현 부재 → import 실패
import VersionList from "../src/components/VersionList";

// service.list_versions 실제 응답 키로 구성한 mock(의도적으로 버킷 순서 뒤섞음)
const shuffled = [
  { version: "inc1", bucket: "incomplete", ready: false, gate_passed: null, bholdout_util: null, has_mlflow: false },
  { version: "arch1", bucket: "archived", ready: true, gate_passed: true, bholdout_util: 0.21, has_mlflow: true },
  { version: "champ", bucket: "champion", ready: true, gate_passed: true, bholdout_util: 0.30, has_mlflow: true },
  { version: "chal1", bucket: "challenger", ready: true, gate_passed: false, bholdout_util: 0.18, has_mlflow: true },
];

describe("VersionList 버킷 정렬 (성공기준 1)", () => {
  it("champion→challenger→archived→incomplete 순으로 렌더한다", () => {
    render(<VersionList versions={shuffled} />);
    const rows = screen.getAllByTestId("version-row");
    expect(rows.map((r) => r.getAttribute("data-bucket"))).toEqual([
      "champion",
      "challenger",
      "archived",
      "incomplete",
    ]);
  });
});

describe("archived 빔 정직성 (성공기준 4, mn1)", () => {
  it("archived 버킷이 비면 '거짓 복원 안 함' 안내를 표시한다", () => {
    const noArchived = shuffled.filter((v) => v.bucket !== "archived");
    render(<VersionList versions={noArchived} />);
    // handoff: "콘솔 도입 이전 이력 없음 — 거짓 복원하지 않음" / 성공기준4: "이전 이력 없음(거짓 복원 안 함)"
    expect(screen.getByText(/거짓 복원/)).toBeInTheDocument();
  });

  it("archived 행이 있으면 그 안내를 표시하지 않는다", () => {
    render(<VersionList versions={shuffled} />);
    expect(screen.queryByText(/거짓 복원/)).not.toBeInTheDocument();
  });
});
