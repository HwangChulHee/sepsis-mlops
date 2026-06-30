"""CLI — 여러 환자 .psv 를 서빙 /predict 에 **동시에** 재생한다(병동 데모, 라운드 다 §6).

배선: glob/목록의 .psv → 각자 유일 run_suffix 로 PsvRowSource → 공유 HttpSender → replay_many.
(가)의 단일 환자 CLI(replay_patient.py)를 N명 동시로 확장한 것.

위험도 선을 Grafana 에서 보려면 서버를 `SERVE_PER_PATIENT_GAUGE=1` 로 띄워야 한다(serve_pred_prob_latest
는 카디널리티 footgun이라 기본 OFF — 라운드 다 §2). 패널: drift 대시보드 "Per-patient risk (latest p)".

사용:
  SERVE_PER_PATIENT_GAUGE=1  # (서버 쪽 env)
  python scripts/replay_ward.py --glob "data/.../p0000*.psv" --speed 7200 --base-url http://localhost:8000
  python scripts/replay_ward.py --psv a.psv --psv b.psv --limit 8
"""
from __future__ import annotations

import argparse
import glob as globlib
import time
import uuid

from sepsis.replay.http_sender import HttpSender
from sepsis.replay.orchestrator import replay_many
from sepsis.replay.psv_source import PsvRowSource


def main() -> int:
    ap = argparse.ArgumentParser(description="여러 환자 .psv 를 서빙 /predict 에 동시 재생(병동)")
    ap.add_argument("--glob", default=None, help="환자 .psv glob 패턴(예: 'data/.../p*.psv')")
    ap.add_argument("--psv", action="append", default=[], help="개별 .psv 경로(반복 가능)")
    ap.add_argument("--base-url", default="http://localhost:8000", help="서빙 베이스 URL")
    ap.add_argument("--speed", type=float, default=3600.0, help="시간 압축비(>0). 3600=1시간을1초")
    ap.add_argument("--featureset", default="vitals", help="vitals | vitals_labs")
    ap.add_argument("--limit", type=int, default=None, help="동시 환자 수 상한(카디널리티 절제)")
    ap.add_argument("--max-workers", type=int, default=None, help="동시 스레드 수(기본=환자 수)")
    args = ap.parse_args()

    paths = list(args.psv)
    if args.glob:
        paths += sorted(globlib.glob(args.glob))
    if not paths:
        ap.error("재생할 .psv 가 없다 — --glob 또는 --psv 를 지정하라.")
    if args.limit is not None:
        paths = paths[: args.limit]

    # 각 환자에 유일 run_suffix → 재실행 stale state(F4) 회피 + 동시 중복 patient_id 방지(F-c1).
    run = uuid.uuid4().hex[:6]
    sources = [
        PsvRowSource(p, featureset=args.featureset, run_suffix=f"{run}-{i}")
        for i, p in enumerate(paths)
    ]
    print(f"replaying {len(sources)} patients  fs={args.featureset}  speed={args.speed}  -> {args.base_url}")
    for s in sources:
        print(f"  - {s.patient_id}")

    with HttpSender(args.base_url) as sender:
        results = replay_many(sources, sender, speed=args.speed, sleep_fn=time.sleep,
                              max_workers=args.max_workers)

    for s, resp in zip(sources, results):
        last = resp[-1] if resp else {}
        print(f"done {s.patient_id}: {len(resp)} steps  last_p={last.get('p')}  last_alarm={last.get('alarm')}")
    print(f"all done. {sum(len(r) for r in results)} timesteps across {len(sources)} patients.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
