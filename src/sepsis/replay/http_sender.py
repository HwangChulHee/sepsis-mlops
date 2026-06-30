"""httpx sender — 행 하나를 서빙 /predict 에 POST 한다 (핸드오프 §3, §6).

계약(§2): body = {"patient_id": str, "features": dict[str, float|None]}.
결측은 null 그대로 보낸다(0/평균 채움 금지 — 그건 서버 몫). 알 수 없는 키는 서버가 422 로
막으므로, 어댑터가 featureset 키만 담아 보내는 것을 전제한다.

httpx.Client 를 주입 가능하게 둬서 테스트가 fake transport 를 끼울 수 있다(이 라운드 RED 는
가짜 sender 로 충분해 이 클래스를 직접 안 쓰지만, CLI 실측·다음 라운드 E2E 에서 쓴다).
"""
from __future__ import annotations

import httpx


class HttpSender:
    """{base_url}/predict 로 POST 하는 Sender. send(patient_id, features)->응답 dict."""

    def __init__(self, base_url: str = "http://localhost:8000", client: httpx.Client | None = None,
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        # 주입된 client 우선(테스트용). 없으면 자체 생성(소유권 보유 → close 책임).
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def send(self, patient_id: str, features: dict[str, float | None]) -> dict:
        # None 값은 JSON null 로 직렬화되어 그대로 전송(서버가 None→np.nan, §2). 채우지 않는다.
        resp = self._client.post(
            f"{self.base_url}/predict",
            json={"patient_id": patient_id, "features": features},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HttpSender":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
