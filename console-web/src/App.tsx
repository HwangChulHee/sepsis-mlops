/**
 * 통합 콘솔 루트 (와이어프레임).
 *   StatusBar(active + 전파배지 transient) + VersionList(버킷 정렬).
 *   상태는 React 훅만. API base = 상대경로 /console/* (Ingress 동일출처).
 */
import { useEffect, useState } from "react";
import { getVersions } from "./api";
import type { VersionsResponse, WriteResult, ApiError } from "./api";
import StatusBar from "./components/StatusBar";
import VersionList from "./components/VersionList";

const FEATURESET = "vitals"; // vitals MVP 한정(mn4)

export default function App() {
  const [data, setData] = useState<VersionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // propagation 은 쓰기 직후 transient(MJ1) — 새로고침 후엔 미상이므로 초기값 null.
  // 쓰기 성공 콜백(VersionList→VersionRow→VersionDetail→ConfirmDialog)에서 setter 로 채운다.
  const [propagation, setPropagation] = useState<"confirmed" | "pending" | null>(null);

  const reload = () => {
    setLoading(true);
    getVersions(FEATURESET)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Partial<ApiError>) =>
        setError(e?.detail || "버전 목록을 불러오지 못했습니다")
      )
      .finally(() => setLoading(false));
  };

  useEffect(reload, []);

  // 쓰기 성공 시: 전파배지 갱신 + 목록 재조회(active·버킷 갱신).
  const onWrite = (r: WriteResult) => {
    setPropagation(r.propagation);
    reload();
  };

  return (
    <div className="app">
      <header className="app__header">
        <h1>Sepsis 운영 콘솔</h1>
        <StatusBar
          featureset={FEATURESET}
          active={data?.active ?? null}
          propagation={propagation}
          loading={loading && !data}
        />
      </header>
      <main className="app__main">
        {error && <p className="app__error">{error}</p>}
        {data && (
          <VersionList versions={data.versions} featureset={FEATURESET} onWrite={onWrite} />
        )}
      </main>
    </div>
  );
}
