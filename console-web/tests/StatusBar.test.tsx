/**
 * 활성 상태바 + 전파 배지 (성공기준 1·3)
 * 권위: handoff_frontend.md StatusBar(active=null → "심링크 소실" 명시, mr2-1)
 *       + 프론트고유계약 2 / 성공기준 3(propagation "pending"→전파대기·"confirmed"→활성확정)
 *       src/sepsis/console/service.py list_versions.active(stripped or None),
 *       approve/rollback 반환 propagation = "confirmed"|"pending"
 *
 * 출제자 정의 모듈 계약: console-web/src/components/StatusBar.tsx default export.
 *   props: { featureset: string; active: string | null; propagation?: "confirmed"|"pending"|null }
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
// RED: 구현 부재 → import 실패
import StatusBar from "../src/components/StatusBar";

describe("StatusBar active 표기 (성공기준 1, mr2-1)", () => {
  it("active 가 있으면 활성 버전을 표시한다", () => {
    render(<StatusBar featureset="vitals" active="champ" />);
    expect(screen.getByText(/champ/)).toBeInTheDocument();
  });

  it("active=null(심링크 소실)이면 '심링크 소실' 명시 표기한다", () => {
    render(<StatusBar featureset="vitals" active={null} />);
    // handoff/성공기준1: "활성 alias 없음(심링크 소실)"
    expect(screen.getByText(/심링크 소실/)).toBeInTheDocument();
  });
});

describe("StatusBar 전파 배지 (성공기준 3, 결정 2-A-c)", () => {
  it("propagation='pending' → '전파 대기' 구분 배지", () => {
    render(<StatusBar featureset="vitals" active="champ" propagation="pending" />);
    expect(screen.getByText(/전파 대기/)).toBeInTheDocument();
  });

  it("propagation='confirmed' → '활성 확정' 표시", () => {
    render(<StatusBar featureset="vitals" active="champ" propagation="confirmed" />);
    expect(screen.getByText(/활성 확정/)).toBeInTheDocument();
  });

  it("propagation 미상(새로고침 후)이면 '전파 대기'/'활성 확정' 어느 쪽도 단정하지 않는다", () => {
    // MJ1: 읽기 엔드포인트는 propagation 미반환 → transient 소실 시 배지 비움/상태 미상
    render(<StatusBar featureset="vitals" active="champ" />);
    expect(screen.queryByText(/전파 대기/)).not.toBeInTheDocument();
    expect(screen.queryByText(/활성 확정/)).not.toBeInTheDocument();
  });
});
