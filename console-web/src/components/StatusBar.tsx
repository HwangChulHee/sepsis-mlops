/**
 * 상단 활성(champion) 상태바 (성공기준 1·3, mr2-1·MJ1).
 *   active(읽기=list_versions.active, stripped). active=null(심링크 소실)이면 명시 표기.
 *   propagation 배지는 쓰기 직후 transient — approve/rollback 응답에서만 옴(MJ1).
 *   새로고침/새 탭 후엔 propagation 미상 → 배지를 비운다(읽기 출처 없음).
 */

type Propagation = "confirmed" | "pending" | null;

interface Props {
  featureset: string;
  active: string | null;
  propagation?: Propagation;
}

export default function StatusBar({ featureset, active, propagation }: Props) {
  return (
    <div className="status-bar">
      <span className="status-bar__fs">{featureset}</span>
      {active !== null ? (
        <span className="status-bar__active">활성: {active}</span>
      ) : (
        <span className="status-bar__active status-bar__active--missing">
          활성 alias 없음(심링크 소실)
        </span>
      )}
      {propagation === "pending" && (
        <span className="badge badge--warn" role="status">
          전파 대기/실패
        </span>
      )}
      {propagation === "confirmed" && (
        <span className="badge badge--ok" role="status">
          활성 확정
        </span>
      )}
    </div>
  );
}
