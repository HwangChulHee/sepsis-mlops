/**
 * 버전 리스트 — 버킷 정렬 + archived 빔 정직성 (성공기준 1·4, mn1).
 *   정렬: champion → challenger → archived → incomplete.
 *   archived 버킷이 비면 "콘솔 도입 이전 이력 없음 — 거짓 복원하지 않음" 안내(빈 목록을 오류로 보이지 않게).
 *   각 행은 data-testid="version-row" + data-bucket 노출(정렬 검증 훅).
 */
import type { VersionSummary, WriteResult } from "../api";
import VersionRow from "./VersionRow";

interface Props {
  versions: VersionSummary[];
  featureset?: string;
  onWrite?: (r: WriteResult) => void;
}

const BUCKET_ORDER: Record<string, number> = {
  champion: 0,
  challenger: 1,
  archived: 2,
  incomplete: 3,
};

const rank = (bucket: string): number =>
  bucket in BUCKET_ORDER ? BUCKET_ORDER[bucket] : 99;

export default function VersionList({ versions, featureset = "vitals", onWrite }: Props) {
  // 빈 목록(버전 자체가 0개)은 archived-없음 안내와 구분 — 별도 빈 상태.
  if (versions.length === 0) {
    return (
      <div className="version-list">
        <p className="version-list__empty">버전 없음</p>
      </div>
    );
  }

  const sorted = [...versions].sort((a, b) => rank(a.bucket) - rank(b.bucket));
  const hasArchived = versions.some((v) => v.bucket === "archived");

  return (
    <div className="version-list">
      {!hasArchived && (
        <p className="version-list__notice">
          콘솔 도입 이전 이력 없음 — 거짓 복원하지 않음
        </p>
      )}
      {sorted.map((v) => (
        <div key={v.version} data-testid="version-row" data-bucket={v.bucket}>
          <VersionRow v={v} featureset={featureset} onWrite={onWrite} />
        </div>
      ))}
    </div>
  );
}
