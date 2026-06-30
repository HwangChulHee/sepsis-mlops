/**
 * 통합 콘솔 루트 (와이어프레임).
 *   StatusBar(active + 전파배지 transient) + VersionList(버킷 정렬).
 *   상태는 React 훅만. API base = 상대경로 /console/* (Ingress 동일출처).
 */
import { useEffect, useState } from "react";
import { getVersions } from "./api";
import type { VersionsResponse } from "./api";
import StatusBar from "./components/StatusBar";
import VersionList from "./components/VersionList";

const FEATURESET = "vitals"; // vitals MVP 한정(mn4)

export default function App() {
  const [data, setData] = useState<VersionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // propagation 은 쓰기 직후 transient(MJ1) — 새로고침 후엔 미상이므로 초기값 null.
  const [propagation] = useState<"confirmed" | "pending" | null>(null);

  const reload = () => {
    getVersions(FEATURESET)
      .then(setData)
      .catch(() => setError("버전 목록을 불러오지 못했습니다"));
  };

  useEffect(reload, []);

  return (
    <div className="app">
      <header className="app__header">
        <h1>Sepsis 운영 콘솔</h1>
        <StatusBar
          featureset={FEATURESET}
          active={data?.active ?? null}
          propagation={propagation}
        />
      </header>
      <main className="app__main">
        {error && <p className="app__error">{error}</p>}
        {data && <VersionList versions={data.versions} featureset={FEATURESET} />}
      </main>
    </div>
  );
}
