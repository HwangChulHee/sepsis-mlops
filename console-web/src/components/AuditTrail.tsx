/**
 * 이 버전 관련 감사 (mn2).
 *   서버 /console/audit 은 fs 필터만(api.py:46) — 버전 단위 서버 필터 없음.
 *   "이 버전 관련"은 클라가 from_version/to_version === 디렉토리명 으로 거른다.
 *   비교 키는 audit 행이 디렉토리명이므로 toDirName(fs, version) 으로 맞춘다(stripped 직접 비교 금지, B1 정합).
 */
import { useEffect, useState } from "react";
import { getAudit, toDirName } from "../api";
import type { AuditEvent } from "../api";

interface Props {
  featureset: string;
  version: string; // stripped
}

export default function AuditTrail({ featureset, version }: Props) {
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const dirName = toDirName(featureset, version);

  useEffect(() => {
    let alive = true;
    getAudit({ fs: featureset })
      .then((all) => {
        if (!alive) return;
        // 클라이언트 측 버전 필터 — 디렉토리명 매칭(mn2).
        setRows(all.filter((e) => e.from_version === dirName || e.to_version === dirName));
      })
      .catch(() => alive && setRows([]));
    return () => {
      alive = false;
    };
  }, [featureset, version, dirName]);

  if (rows.length === 0) {
    return <p className="audit-trail audit-trail--empty">이 버전 관련 감사 기록 없음</p>;
  }

  return (
    <ul className="audit-trail">
      {rows.map((e) => (
        <li key={e.id} className="audit-trail__row">
          <span className="audit-trail__ts">{e.ts ?? "—"}</span>
          <span className="audit-trail__type">{e.event_type}</span>
          <span className="audit-trail__gate">
            {e.gate_passed === true ? "PASS" : e.gate_passed === false ? "REGRESSED" : "—"}
          </span>
          <span className="audit-trail__actor">{e.actor_unverified ?? "—"}</span>
          <span className="audit-trail__reason">{e.reason ?? ""}</span>
        </li>
      ))}
    </ul>
  );
}
