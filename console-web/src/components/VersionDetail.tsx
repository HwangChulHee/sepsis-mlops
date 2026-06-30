/**
 * 펼친 버전 상세 — get_version_detail lazy 호출 후 게이트/재학습/액션/감사 (와이어프레임).
 *   읽기 경로의 version 은 stripped 그대로 사용(getDetail). 쓰기는 ConfirmDialog→api 가 재부착(B1).
 */
import { useEffect, useState } from "react";
import { getDetail } from "../api";
import type { VersionDetailResponse, WriteResult } from "../api";
import GatePanel from "./GatePanel";
import RetrainPanel from "./RetrainPanel";
import Actions from "./Actions";
import MlflowLink from "./MlflowLink";
import AuditTrail from "./AuditTrail";
import ConfirmDialog from "./ConfirmDialog";

interface Props {
  featureset: string;
  version: string; // stripped
  bucket: string;
}

export default function VersionDetail({ featureset, version, bucket }: Props) {
  const [detail, setDetail] = useState<VersionDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"approve" | "rollback" | null>(null);
  const [result, setResult] = useState<WriteResult | null>(null);

  useEffect(() => {
    let alive = true;
    getDetail(featureset, version)
      .then((d) => alive && setDetail(d))
      .catch(() => alive && setError("상세를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
  }, [featureset, version]);

  if (error) return <div className="version-detail version-detail--error">{error}</div>;
  if (!detail) return <div className="version-detail version-detail--loading">불러오는 중…</div>;

  return (
    <div className="version-detail">
      <GatePanel gate={detail.gate} />
      <RetrainPanel retrain={detail.retrain} />
      <Actions
        bucket={bucket}
        gatePassed={detail.gate?.no_regression ?? null}
        ready={detail.ready}
        onApprove={() => setDialog("approve")}
        onRollback={() => setDialog("rollback")}
      />
      <MlflowLink href={detail.mlflow_link} />
      {result && (
        <p className="version-detail__result">
          전파: {result.propagation === "confirmed" ? "활성 확정" : "전파 대기/실패"}
        </p>
      )}
      {dialog && (
        <ConfirmDialog
          fs={featureset}
          version={version}
          action={dialog}
          onResult={(r) => {
            setResult(r);
            setDialog(null);
          }}
        />
      )}
      <AuditTrail featureset={featureset} version={version} />
    </div>
  );
}
