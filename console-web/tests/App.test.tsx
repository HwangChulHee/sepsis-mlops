/**
 * App 통합 — 쓰기 후 전파배지 배선 + 목록 새로고침 (H1·H2 결함 회귀방지)
 * 권위: handoff_frontend.md StatusBar(propagation transient, MJ1) / 성공기준 3
 *       콜백 체인 VersionList→VersionRow→VersionDetail→ConfirmDialog→App(setPropagation+reload)
 *
 * 통합테스트 부재가 "전파배지 미배선·쓰기후 미새로고침" 결함을 못 잡은 근본 원인.
 * 여기서는 ../src/api 를 모킹해 행 클릭→상세 lazy 로드→승인→배지+reload 를 한 번에 검증한다.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getVersions = vi.fn();
const getDetail = vi.fn();
const getAudit = vi.fn();
const approve = vi.fn();
const rollback = vi.fn();
vi.mock("../src/api", () => ({
  getVersions: (...a: unknown[]) => getVersions(...a),
  getDetail: (...a: unknown[]) => getDetail(...a),
  getAudit: (...a: unknown[]) => getAudit(...a),
  approve: (...a: unknown[]) => approve(...a),
  rollback: (...a: unknown[]) => rollback(...a),
  toDirName: (fs: string, v: string) => (v.startsWith(`gru_${fs}@`) ? v : `gru_${fs}@${v}`),
}));

import App from "../src/App";

const versionsResp = (active: string | null) => ({
  featureset: "vitals",
  active,
  versions: [
    {
      version: "chal1",
      bucket: "challenger",
      ready: true,
      gate_passed: true,
      bholdout_util: 0.3,
      has_mlflow: true,
    },
  ],
});

const detailResp = {
  version: "chal1",
  bucket: "challenger",
  ready: true,
  gate: { no_regression: true, bholdout_util: 0.3, new_aval_util: 0.28, old_aval_util: 0.25 },
  retrain: {},
  meta: { featureset: "vitals" },
  mlflow_link: null,
};

beforeEach(() => {
  getVersions.mockReset();
  getDetail.mockReset();
  getAudit.mockReset();
  approve.mockReset();
  rollback.mockReset();
  getAudit.mockResolvedValue([]);
});

describe("App 통합 — 행 클릭→상세 lazy→승인→전파배지+reload", () => {
  it("승인 성공 시 StatusBar 에 전파배지가 뜨고 getVersions 가 재호출된다", async () => {
    // 최초 active=null(심링크 소실 가정), 쓰기 후 reload 에서 active 채워짐.
    getVersions
      .mockResolvedValueOnce(versionsResp(null))
      .mockResolvedValueOnce(versionsResp("chal1"));
    getDetail.mockResolvedValue(detailResp);
    approve.mockResolvedValue({
      event_id: 1,
      prev: null,
      active: "gru_vitals@chal1",
      propagation: "confirmed",
    });

    const user = userEvent.setup();
    render(<App />);

    // 초기 목록 로드 대기.
    const row = await screen.findByTestId("version-row");
    // 행(헤더 버튼) 클릭 → VersionDetail lazy 로드.
    await user.click(within(row).getByRole("button", { name: /chal1/ }));

    // 상세의 승인 버튼(활성) 클릭 → ConfirmDialog 표시.
    const approveBtn = await screen.findByRole("button", { name: /승인/ });
    await user.click(approveBtn);

    // 다이얼로그에서 actor 입력 후 확인.
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByPlaceholderText("actor"), "alice");
    await user.click(within(dialog).getByRole("button", { name: /확인/ }));

    // 전파배지("활성 확정")가 StatusBar(헤더 영역)에 표시.
    const header = screen.getByRole("banner");
    expect(await within(header).findByText(/활성 확정/)).toBeInTheDocument();
    // reload — getVersions 가 두 번째로 호출됨(active·버킷 갱신).
    await vi.waitFor(() => expect(getVersions).toHaveBeenCalledTimes(2));
  });

  it("초기 로딩 중에는 '심링크 소실' 오경보 대신 '불러오는 중'을 표기한다", async () => {
    // getVersions 가 아직 resolve 안 된 상태(pending)에서 즉시 렌더 — active=null 이지만 로딩.
    let resolve!: (v: unknown) => void;
    getVersions.mockReturnValue(new Promise((r) => (resolve = r)));

    render(<App />);
    // 데이터 도착 전: 로딩 표기, 심링크 소실 경보는 아직 아님.
    expect(screen.getByText(/불러오는 중/)).toBeInTheDocument();
    expect(screen.queryByText(/심링크 소실/)).not.toBeInTheDocument();

    // 응답이 실제로 active:null 이면 그때 심링크 소실 경보.
    resolve(versionsResp(null));
    expect(await screen.findByText(/심링크 소실/)).toBeInTheDocument();
  });
});
