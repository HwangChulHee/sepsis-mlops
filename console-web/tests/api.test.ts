/**
 * API 클라이언트 계약 (성공기준 5 + API 클라 분기)
 * 권위: docs/design/console/handoff_frontend.md (구현1 항목5·B1, api.ts 의사코드)
 *       src/sepsis/console/api.py (POST 경로·WriteRequest 스키마·422/403 분기)
 *       src/sepsis/console/service.py (_require_consistent: gru_<fs>@ 접두 강제)
 *
 * 출제자 정의 모듈 계약: console-web/src/api.ts 는 아래를 export 한다.
 *   toDirName(fs, version): string                  // B1 접두 재부착
 *   getVersions(fs): Promise<...>
 *   getDetail(fs, version): Promise<...>
 *   approve(fs, version, actor, reason): Promise<{event_id,prev,active,propagation}>
 *   rollback(fs, version, actor, reason): Promise<...>
 * 쓰기 실패 시 reject 값은 { status:number, detail:string } 를 노출한다(상태코드 분기용).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
// RED: console-web/src/api.ts 는 아직 없다 → import 실패로 RED.
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

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("toDirName — B1 읽기 stripped / 쓰기 dir name (성공기준 5)", () => {
  it("stripped 순수버전에 gru_<fs>@ 접두를 재부착한다", () => {
    expect(api.toDirName("vitals", "champ")).toBe("gru_vitals@champ");
  });

  it("이미 디렉토리명이면 이중접두 없이 그대로 둔다", () => {
    expect(api.toDirName("vitals", "gru_vitals@champ")).toBe("gru_vitals@champ");
  });
});

describe("approve — 쓰기 요청 body version = 디렉토리명 (성공기준 5)", () => {
  it("POST /console/approve 로 version='gru_<fs>@<v>' 를 전송한다", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      status: 200,
      json: { event_id: 1, prev: null, active: "gru_vitals@champ", propagation: "confirmed" },
    });

    const res = await api.approve("vitals", "champ", "alice", "ship it");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/console/approve");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    // 읽기 응답은 stripped("champ")였지만 쓰기 body는 디렉토리명이어야 한다(B1 비대칭).
    expect(body.version).toBe("gru_vitals@champ");
    expect(body.fs).toBe("vitals");
    expect(body.actor).toBe("alice");
    expect(body.reason).toBe("ship it");
    // 성공 응답 propagation 통과
    expect(res.propagation).toBe("confirmed");
  });
});

describe("rollback — 쓰기 요청 body version = 디렉토리명 (성공기준 5)", () => {
  it("POST /console/rollback 로 version='gru_<fs>@<v>' 를 전송한다", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      status: 200,
      json: { event_id: 2, prev: "gru_vitals@champ", active: "gru_vitals@old", propagation: "pending" },
    });

    await api.rollback("vitals", "old", "bob", "regression");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/console/rollback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body).version).toBe("gru_vitals@old");
  });
});

describe("읽기 경로 — getVersions/getDetail URL (계약 정합)", () => {
  it("getVersions 는 /console/versions?fs= 를 GET 한다", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      status: 200,
      json: { featureset: "vitals", active: "champ", versions: [] },
    });
    await api.getVersions("vitals");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/console/versions?fs=vitals");
  });

  it("getDetail 은 읽기 stripped 버전을 경로에 그대로 쓴다", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      status: 200,
      json: { version: "champ", bucket: "champion" },
    });
    await api.getDetail("vitals", "champ");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/console/versions/champ");
    expect(url).toContain("fs=vitals");
  });
});

describe("쓰기 실패 분기 — 422 게이트 / 403 미승인 (API 클라 분기)", () => {
  it("422 → 게이트(미통과)/미완성/교차-fs 분기: status=422 + detail 표면화", async () => {
    // api.py:58-59 ValueError/FileNotFoundError → 422, detail=str(e)
    mockFetchOnce({
      ok: false,
      status: 422,
      json: { detail: "version 'champ' not in featureset 'vitals'" },
    });
    await expect(api.approve("vitals", "champ", "alice", "")).rejects.toMatchObject({
      status: 422,
      detail: "version 'champ' not in featureset 'vitals'",
    });
  });

  it("403 → 미승인 분기: status=403", async () => {
    // api.py:60-61 PermissionError → 403 (콘솔 경로 dead path지만 클라는 분기 보유)
    mockFetchOnce({
      ok: false,
      status: 403,
      json: { detail: "approved is not True" },
    });
    await expect(api.approve("vitals", "gru_vitals@champ", "alice", "")).rejects.toMatchObject({
      status: 403,
    });
  });
});
