/**
 * 승인/롤백 버튼 게이팅 (성공기준 2·2b, mn1·BR2-1).
 *   승인: gate_passed !== true || !ready → disabled (incomplete의 null 포함, M3 이중게이트).
 *   롤백: bucket === "archived"(과거활성)에만 활성 — 백엔드 롤백은 무게이트라 프론트가 1차 방어선.
 */

interface Props {
  bucket: string;
  gatePassed: boolean | null;
  ready: boolean;
  onApprove?: () => void;
  onRollback?: () => void;
}

export default function Actions({ bucket, gatePassed, ready, onApprove, onRollback }: Props) {
  // gate_passed === false 만 보면 incomplete의 null 이 통과해 헛클릭(mn1) → !== true 로 사전차단.
  const approveDisabled = gatePassed !== true || !ready;
  // BR2-1: 롤백은 과거활성(archived)만. challenger/incomplete/champion 차단.
  const rollbackDisabled = bucket !== "archived";

  return (
    <div className="actions">
      <button
        type="button"
        className="actions__approve"
        onClick={onApprove}
        disabled={approveDisabled}
        title={approveDisabled ? "게이트 미통과/미완성 — 승인 불가" : undefined}
      >
        승인
      </button>
      <button
        type="button"
        className="actions__rollback"
        onClick={onRollback}
        disabled={rollbackDisabled}
        title={rollbackDisabled ? "롤백은 과거활성(archived) 버전만 가능" : undefined}
      >
        롤백
      </button>
    </div>
  );
}
