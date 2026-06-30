/**
 * 승인 시도 시 백엔드 422 메시지 화면 표면화 (성공기준 2 후반)
 * 권위: handoff_frontend.md 프론트고유계약 1·5 / 성공기준 2("백엔드 422 메시지 표면화")
 *       src/sepsis/console/api.py: ValueError/FileNotFoundError → 422, detail=str(e)
 *
 * 출제자 정의 모듈 계약: console-web/src/components/ConfirmDialog.tsx default export.
 *   props: { fs: string; version: string; action: "approve"|"rollback";
 *            onResult?: (r: {propagation: string}) => void }
 *   actor 입력 placeholder="actor", reason placeholder="reason", 확정 버튼 name=/확인|승인/.
 *   확정 시 ../src/api 의 approve/rollback 을 toDirName 재부착된 version 으로 호출하고,
 *   reject({status,detail}) 면 detail 을 화면에 표면화한다.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ../src/api 를 모킹(구현 모듈은 본 출제 단계에서 만들지 않음 → 미존재로도 RED).
const approveMock = vi.fn();
const rollbackMock = vi.fn();
vi.mock("../src/api", () => ({
  approve: (...a: unknown[]) => approveMock(...a),
  rollback: (...a: unknown[]) => rollbackMock(...a),
  toDirName: (fs: string, v: string) => (v.startsWith(`gru_${fs}@`) ? v : `gru_${fs}@${v}`),
}));

// RED: ConfirmDialog 구현 부재 → import 실패
import ConfirmDialog from "../src/components/ConfirmDialog";

beforeEach(() => {
  approveMock.mockReset();
  rollbackMock.mockReset();
});

describe("ConfirmDialog 422 표면화 (성공기준 2)", () => {
  it("승인 거부(422)면 백엔드 detail 메시지를 화면에 표면화한다", async () => {
    approveMock.mockRejectedValue({
      status: 422,
      detail: "gru_vitals@chal1 is not ready (no .ready marker)",
    });
    const user = userEvent.setup();
    render(<ConfirmDialog fs="vitals" version="chal1" action="approve" />);

    await user.type(screen.getByPlaceholderText("actor"), "alice");
    await user.click(screen.getByRole("button", { name: /확인|승인/ }));

    expect(approveMock).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText(/is not ready \(no \.ready marker\)/)
    ).toBeInTheDocument();
  });

  it("승인 성공이면 onResult 로 propagation 을 전달한다", async () => {
    approveMock.mockResolvedValue({
      event_id: 1,
      prev: null,
      active: "gru_vitals@chal1",
      propagation: "pending",
    });
    const onResult = vi.fn();
    const user = userEvent.setup();
    render(<ConfirmDialog fs="vitals" version="chal1" action="approve" onResult={onResult} />);

    await user.type(screen.getByPlaceholderText("actor"), "alice");
    await user.click(screen.getByRole("button", { name: /확인|승인/ }));

    // B1: 쓰기 직전 디렉토리명 재부착 — approve 의 version 인자는 stripped "chal1" 이지만
    //     ConfirmDialog→api.approve 호출은 fs/stripped 를 넘기고 api 가 toDirName 으로 재부착한다.
    expect(approveMock).toHaveBeenCalledWith("vitals", "chal1", "alice", expect.anything());
    await vi.waitFor(() => expect(onResult).toHaveBeenCalledWith(
      expect.objectContaining({ propagation: "pending" })
    ));
  });
});
