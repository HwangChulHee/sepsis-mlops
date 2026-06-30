/**
 * 승인/롤백 확정 다이얼로그 (성공기준 2 후반).
 *   actor·reason 입력 → api.approve/rollback 호출(version 은 stripped 표면값; api 가 toDirName 재부착, B1).
 *   422 reject 시 백엔드 detail 메시지를 화면에 표면화(이중 게이트의 사용자 피드백).
 *   성공 시 onResult 로 propagation 전달.
 */
import { useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { WriteResult, ApiError } from "../api";

interface Props {
  fs: string;
  version: string; // stripped 표면값 — api 가 toDirName 으로 디렉토리명 재부착
  action: "approve" | "rollback";
  onResult?: (r: WriteResult) => void;
  onCancel?: () => void;
}

export default function ConfirmDialog({ fs, version, action, onResult, onCancel }: Props) {
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const actorRef = useRef<HTMLInputElement>(null);

  // 열릴 때 actor 입력으로 포커스 이동(접근성).
  useEffect(() => {
    actorRef.current?.focus();
  }, []);

  const submit = async () => {
    setError(null);
    setBusy(true);
    const call = action === "approve" ? api.approve : api.rollback;
    try {
      const res = await call(fs, version, actor, reason);
      onResult?.(res);
    } catch (e) {
      // 422=게이트/미완성/교차-fs, 403=미승인. detail 을 그대로 표면화.
      const err = e as Partial<ApiError>;
      setError(err?.detail || "요청이 거부되었습니다");
    } finally {
      setBusy(false);
    }
  };

  const label = action === "approve" ? "승인 확인" : "롤백 확인";

  return (
    <div
      className="confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-label={label}
    >
      <input
        ref={actorRef}
        className="confirm-dialog__actor"
        placeholder="actor"
        value={actor}
        onChange={(e) => setActor(e.target.value)}
      />
      <input
        className="confirm-dialog__reason"
        placeholder="reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button type="button" onClick={submit} disabled={busy || !actor.trim()}>
        확인
      </button>
      <button type="button" className="confirm-dialog__cancel" onClick={onCancel} disabled={busy}>
        취소
      </button>
      {error && (
        <p className="confirm-dialog__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
