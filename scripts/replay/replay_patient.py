"""CLI — 환자 .psv 한 명을 서빙 /predict 에 재생 버튼처럼 흘려보낸다 (핸드오프 §3.3).

배선: PsvRowSource(.psv) → HttpSender(base_url) → replay_stream(speed, time.sleep).
행마다 응답(p, alarm)을 표준출력에 한 줄씩 찍어 데모 가시성을 준다.

F4(재실행 stale state) 회피: 서버엔 hidden-state 리셋 엔드포인트가 없어, 같은 patient_id 로
다시 틀면 서버가 이전 실행 상태를 이어받아 위험도 곡선이 오염된다. 그래서 --run-suffix 기본값을
매 실행 유일한 짧은 토큰으로 잡아 patient_id 를 새로 찍는다(같은 .psv 라도 fresh patient).

사용:
  python scripts/replay/replay_patient.py --psv data/.../p000023.psv --base-url http://localhost:8000 --speed 7200
  (minikube 라면 먼저 kubectl port-forward svc/sepsis-serving 8000:80)
"""
from __future__ import annotations

import argparse
import time
import uuid

from sepsis.replay.engine import replay_stream
from sepsis.replay.http_sender import HttpSender
from sepsis.replay.psv_source import PsvRowSource


def main() -> int:
    ap = argparse.ArgumentParser(description="한 환자 .psv 를 서빙 /predict 에 시간순 재생")
    ap.add_argument("--psv", required=True, help="환자 .psv 경로")
    ap.add_argument("--base-url", default="http://localhost:8000", help="서빙 베이스 URL")
    ap.add_argument("--speed", type=float, default=3600.0,
                    help="시간 압축비(3600=1시간을1초, 1=실시간). >0")
    ap.add_argument("--featureset", default="vitals", help="vitals | vitals_labs")
    ap.add_argument("--run-suffix", default=None,
                    help="patient_id 에 붙일 토큰. 기본=짧은 uuid(매 실행 fresh patient, F4 회피)")
    args = ap.parse_args()

    run_suffix = args.run_suffix if args.run_suffix is not None else uuid.uuid4().hex[:8]
    source = PsvRowSource(args.psv, featureset=args.featureset, run_suffix=run_suffix)

    print(f"replaying {source.patient_id}  fs={args.featureset}  speed={args.speed}  -> {args.base_url}")
    with HttpSender(args.base_url) as sender:
        responses = replay_stream(source, sender, speed=args.speed, sleep_fn=time.sleep)

    for i, r in enumerate(responses):
        # 서버 응답 계약(§2): {patient_id, p, alarm, featureset}
        print(f"  t={i:>3}  p={r.get('p'):.4f}  alarm={r.get('alarm')}")
    print(f"done. {len(responses)} timesteps sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
