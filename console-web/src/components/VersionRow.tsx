/**
 * 접힌 버전 행 — 클릭 시 VersionDetail lazy 펼침 (와이어프레임).
 *   접힘: version·bucket 배지·gate_passed 신호·bholdout_util 헤드라인·MLflow 아이콘.
 *   펼침: get_version_detail(stripped 버전) lazy 호출.
 */
import { useState } from "react";
import type { VersionSummary } from "../api";
import VersionDetail from "./VersionDetail";

interface Props {
  v: VersionSummary;
  featureset: string;
}

const gateSignal = (g: boolean | null): string =>
  g === true ? "✓ PASS" : g === false ? "✗ REGRESSED" : "… 미완성";

// 색 구분용 modifier(표시 전용 — 로직/판정에 영향 없음): PASS=ok / REGRESSED=warn / 미완성=muted
const gateClass = (g: boolean | null): string =>
  g === true ? "version-row__gate--ok" : g === false ? "version-row__gate--warn" : "version-row__gate--muted";

export default function VersionRow({ v, featureset }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className={`version-row ${open ? "version-row--open" : ""}`}>
      <button
        type="button"
        className="version-row__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="version-row__version">{v.version}</span>
        <span className={`badge badge--${v.bucket}`}>{v.bucket}</span>
        <span className={`version-row__gate ${gateClass(v.gate_passed)}`}>
          {gateSignal(v.gate_passed)}
        </span>
        <span className="version-row__util">
          {v.bholdout_util !== null ? v.bholdout_util.toFixed(3) : "—"}
        </span>
        {v.has_mlflow && (
          <span className="version-row__mlflow" title="MLflow run 있음">
            ⚲
          </span>
        )}
      </button>
      {open && <VersionDetail featureset={featureset} version={v.version} bucket={v.bucket} />}
    </div>
  );
}
