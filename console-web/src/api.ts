/**
 * /console API 클라이언트 (핸드오프 B 구현1 항목5·B1, api.ts 의사코드 구현).
 *
 * B1 읽기/쓰기 version 비대칭 (확인됨):
 *   - 읽기 응답(getVersions/getDetail)의 version·active 는 stripped 순수버전("champ").
 *   - 쓰기 요청(approve/rollback)의 version 은 디렉토리명("gru_<fs>@champ") 이어야 한다.
 *     백엔드 _require_consistent 가 gru_<fs>@ 접두를 강제(service.py:38-40) → toDirName 으로 재부착.
 * 상태코드 분기 (api.py:58-61): 422 = ValueError/FileNotFoundError(게이트·미완성·교차-fs),
 *   403 = PermissionError(미승인, 콘솔 경로 dead path지만 분기는 보유).
 *   쓰기 실패는 { status, detail } 로 reject 해 호출부가 분기한다.
 */

const BASE: string = import.meta.env.VITE_API_BASE ?? "";

export interface VersionSummary {
  version: string;
  bucket: "champion" | "challenger" | "archived" | "incomplete" | string;
  ready: boolean;
  gate_passed: boolean | null;
  bholdout_util: number | null;
  has_mlflow: boolean;
}

export interface VersionsResponse {
  featureset: string;
  active: string | null;
  versions: VersionSummary[];
}

export interface GateInfo {
  no_regression?: boolean;
  bholdout_util?: number;
  new_aval_util?: number;
  old_aval_util?: number;
  cross_site_claim?: boolean;
  eps?: number;
  validated_at?: string;
  [k: string]: unknown;
}

export interface VersionDetailResponse {
  version: string;
  bucket: string;
  ready: boolean;
  gate: GateInfo;
  retrain: Record<string, unknown>;
  meta: { featureset: string; tau?: number; trained_on?: string };
  mlflow_link?: string | null;
}

export interface WriteResult {
  event_id: number;
  prev: string | null;
  active: string | null;
  propagation: "confirmed" | "pending";
}

export interface AuditEvent {
  id: number;
  ts: string | null;
  event_type: string;
  featureset: string;
  gate_passed: boolean | null;
  from_version: string | null;
  to_version: string | null;
  run_id: string | null;
  git_commit: string | null;
  actor_unverified: string | null;
  verified_subject: string | null;
  reason: string | null;
}

export interface AuditQuery {
  event_type?: string;
  gate_passed?: boolean;
  since?: string;
  fs?: string;
}

export interface ApiError {
  status: number;
  detail: string;
}

/** B1: 쓰기 직전 stripped 순수버전에 gru_<fs>@ 접두 재부착. 이미 dir name이면 그대로(이중접두 방지). */
export const toDirName = (fs: string, version: string): string =>
  version.startsWith(`gru_${fs}@`) ? version : `gru_${fs}@${version}`;

// 읽기 응답도 상태코드 검사: 422/403 이면 detail 을 표면화하며 reject.
// (검사 누락 시 422 의 {detail} 본문이 정상 응답처럼 흘러 VersionList 가 undefined spread 로 크래시.)
// 제네릭: `.then(j)` 호출부의 선언된 반환 타입에서 T 를 추론한다(any 제거, 호출부 타입 유지).
const j = async <T>(res: Response): Promise<T> => {
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string })?.detail ?? "";
    } catch {
      detail = "";
    }
    return Promise.reject<T>({ status: res.status, detail } as ApiError);
  }
  return res.json() as Promise<T>;
};

function qs(q: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  return p.toString();
}

async function post(url: string, body: unknown): Promise<WriteResult> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string })?.detail ?? "";
    } catch {
      detail = "";
    }
    // 422 = 게이트/미완성/교차-fs, 403 = 미승인 — 호출부가 status 로 분기.
    return Promise.reject<WriteResult>({ status: res.status, detail } as ApiError);
  }
  return res.json() as Promise<WriteResult>;
}

// 읽기: stripped 버전을 그대로 경로/쿼리에 사용. j<T> 로 응답 타입을 명시(추론 대신 고정).
export const getVersions = (fs: string): Promise<VersionsResponse> =>
  fetch(`${BASE}/console/versions?fs=${fs}`).then((r) => j<VersionsResponse>(r));

export const getDetail = (fs: string, version: string): Promise<VersionDetailResponse> =>
  fetch(`${BASE}/console/versions/${version}?fs=${fs}`).then((r) => j<VersionDetailResponse>(r));

export const getAudit = (q: AuditQuery): Promise<AuditEvent[]> =>
  fetch(`${BASE}/console/audit?${qs(q as Record<string, unknown>)}`).then((r) => j<AuditEvent[]>(r));

// 쓰기: toDirName 으로 디렉토리명 재부착(B1).
export const approve = (fs: string, version: string, actor: string, reason: string): Promise<WriteResult> =>
  post(`${BASE}/console/approve`, { fs, version: toDirName(fs, version), actor, reason });

export const rollback = (fs: string, version: string, actor: string, reason: string): Promise<WriteResult> =>
  post(`${BASE}/console/rollback`, { fs, version: toDirName(fs, version), actor, reason });
