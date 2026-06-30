/**
 * MLflow 링크 — 있으면 링크, 없으면 폴백 안내 (6-A).
 */

interface Props {
  href?: string | null;
}

export default function MlflowLink({ href }: Props) {
  if (!href) {
    return (
      <span className="mlflow-link mlflow-link--missing">
        MLflow run 연결 없음
      </span>
    );
  }
  return (
    <a className="mlflow-link" href={href} target="_blank" rel="noreferrer">
      MLflow에서 보기
    </a>
  );
}
