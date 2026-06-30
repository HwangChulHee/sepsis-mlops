/**
 * 재학습 메타 패널 — epochs·val_loss·seed·n_*·git_commit (와이어프레임).
 */

interface Props {
  retrain: Record<string, unknown>;
}

const FIELDS: Array<[string, string]> = [
  ["epochs", "epochs"],
  ["val_loss", "val_loss"],
  ["b_split_seed", "B-split seed"],
  ["run_id", "run_id"],
  ["git_commit", "git_commit"],
];

export default function RetrainPanel({ retrain }: Props) {
  return (
    <dl className="retrain-panel">
      {FIELDS.map(([key, label]) => (
        <div key={key} className="retrain-panel__row">
          <dt>{label}</dt>
          <dd>{retrain?.[key] !== undefined ? String(retrain[key]) : "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
