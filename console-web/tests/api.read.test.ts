/**
 * API 읽기 경로 res.ok 검사 + getAudit 쿼리 직렬화 (리뷰 결함 회귀방지)
 * 권위: handoff_frontend.md api.ts 의사코드 / src/sepsis/console/api.py (422/403, detail=str(e))
 *
 * 결함: 읽기 j() 가 상태코드 무검사 → 422 의 {detail} 가 정상 응답처럼 흘러 VersionList undefined spread 크래시.
 * 계약: getVersions/getDetail/getAudit 는 !res.ok 면 reject({status,detail}) 한다.
 *       getAudit 는 gate_passed=false 를 포함해 쿼리스트링을 직렬화한다.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as api from "../src/api";

function mockFetchOnce(opts: { ok: boolean; status: number; json: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: opts.ok,
    status: opts.status,
    json: async () => opts.json,
  });
  vi.stubGlobal("fetch", fetchMock); // 전역 fetch 주입(타입 안전)
  return fetchMock;
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("읽기 res.ok 검사 — 422 → reject({status,detail})", () => {
  it("getVersions 가 422 면 detail 을 표면화하며 reject 한다(정상 응답인 양 통과 금지)", async () => {
    mockFetchOnce({
      ok: false,
      status: 422,
      json: { detail: "featureset 'vitals' not found" },
    });
    await expect(api.getVersions("vitals")).rejects.toMatchObject({
      status: 422,
      detail: "featureset 'vitals' not found",
    });
  });

  it("getDetail 도 422 면 reject 한다", async () => {
    mockFetchOnce({ ok: false, status: 422, json: { detail: "no such version" } });
    await expect(api.getDetail("vitals", "champ")).rejects.toMatchObject({ status: 422 });
  });

  it("getVersions 가 200 이면 본문을 정상 반환한다(회귀 확인)", async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: { featureset: "vitals", active: "champ", versions: [] },
    });
    await expect(api.getVersions("vitals")).resolves.toMatchObject({ active: "champ" });
  });
});

describe("getAudit 쿼리 직렬화 — gate_passed=false 포함", () => {
  it("gate_passed=false 를 쿼리에 직렬화한다(falsy 라고 누락하지 않음)", async () => {
    const fetchMock = mockFetchOnce({ ok: true, status: 200, json: [] });
    await api.getAudit({ fs: "vitals", gate_passed: false });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/console/audit?");
    expect(url).toContain("fs=vitals");
    expect(url).toContain("gate_passed=false");
  });

  it("빈 문자열/undefined 키는 직렬화에서 제외한다", async () => {
    const fetchMock = mockFetchOnce({ ok: true, status: 200, json: [] });
    await api.getAudit({ fs: "vitals", event_type: "", since: undefined });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("fs=vitals");
    expect(url).not.toContain("event_type=");
    expect(url).not.toContain("since=");
  });
});
