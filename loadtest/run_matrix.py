"""run_matrix — N×코어 실험 매트릭스 오케스트레이션 (부하테스트 (나)).

설계: docs/design/load-test/ 결정 4·5·6·7 / 핸드오프 §2.4·§2.5.
**스크립트(TDD 대상 아님)** — 사람이 (가) 스택 up 후 실행한다. 실제 측정 수치는 SM.

칸(cell)마다:
  1. 서버 재시작 + 프리웜 (M-r2-1·m-r3-1) — 서버 pid 상태(_h·_locks·_last) 리셋, lazy-boot 선태움.
  2. 코어·스레드캡 동반 설정 (결정 5) — cpus 와 *_NUM_THREADS 동수.
  3. 측정 오염 방지 (결정 4) — per-patient gauge off, prometheus scrape 15s.
  4. Locust headless 부하 + 워밍업 컷(첫 30s, --reset-stats).
  5. 수집 (결정 6) — RPS·p50/p95/p99·에러율·메모리 + CPU 경고 오라클.

매트릭스: N축 1·10·50·200·500·1000 (코어2) / 코어축 1·2·4 (N200). 십자 ~8칸.
SM-3: N=200 지속 중 /admin/reload → OOM/메모리(순간 증분) 판정, latency 별도 창.

주: 실제 컨테이너 조작(docker compose restart·env 주입)은 배포 환경에 의존하므로
훅(_restart_serving 등)만 정의하고, 실행 오케스트레이션 골격을 제공한다. 세부 명령은
운영 환경에서 채운다(핸드오프 §5 "실제 부하 실행은 SM").
"""
from __future__ import annotations

import dataclasses
import subprocess
import time
import urllib.request

# ── 매트릭스 정의 (결정 5) ──────────────────────────────────────────────────
N_AXIS = [1, 10, 50, 200, 500, 1000]      # 코어 2 고정, 무릎까지 연장
CORE_AXIS = [1, 2, 4]                       # N=200 고정, 스레드캡 동반
REP_CORE = 2                                # N축 대표 코어
REP_N = 200                                 # 코어축 대표 N
WARMUP_CUT_S = 30                           # 램프업 후 컷 창 (M2)
COMPOSE = ["docker", "compose", "-f", "deploy/docker-compose.yml", "--project-directory", "."]
SERVE_URL = "http://localhost:8000"


@dataclasses.dataclass
class Cell:
    n: int
    cores: int
    run_time_s: int = 120
    ramp: int = 50
    is_sm3: bool = False   # SM-3(부하 중 reload) 칸 여부


def cross_matrix() -> list[Cell]:
    """십자 매트릭스 칸 목록(코어2·N200 중복 제거)."""
    cells: list[Cell] = []
    for n in N_AXIS:
        cells.append(Cell(n=n, cores=REP_CORE))
    for c in CORE_AXIS:
        if c == REP_CORE:
            continue                        # (REP_N, REP_CORE)는 N축에 이미 있음
        cells.append(Cell(n=REP_N, cores=c))
    return cells


def _restart_serving(cores: int) -> None:
    """서버 재시작 + 코어/스레드캡 동반 설정 (M-r2-1·결정 5).

    cpus 와 *_NUM_THREADS 를 cores 동수로 맞춰 재기동한다. 실제 env/limits 주입은
    compose override 나 환경변수로 배포 환경에서 채운다(여기선 재시작 훅만).
    """
    # 예시 골격: override 파일/환경으로 cpus=cores, *_NUM_THREADS=cores 주입 후 재기동.
    subprocess.run(COMPOSE + ["restart", "serving"], check=True)


def _prewarm() -> None:
    """프리웜 1건 (m-r3-1) — 첫 /predict lazy-boot(300-trial 캘리브레이션) 선태움.

    /health 를 healthy 될 때까지 폴링(캘리브레이션 완료 신호). 부하 구간 밖에서 소진.
    """
    for _ in range(120):                    # 최대 ~20분(캘리브레이션 예산 여유)
        try:
            with urllib.request.urlopen(f"{SERVE_URL}/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(10)
    raise RuntimeError("serving /health 가 프리웜 창 안에 healthy 되지 않음")


def _run_locust(cell: Cell, out_prefix: str) -> None:
    """Locust headless 실행 + 워밍업 컷(첫 30s는 --reset-stats 로 제외)."""
    cmd = [
        "locust", "-f", "loadtest/locustfile.py",
        "--host", SERVE_URL, "--headless",
        "-u", str(cell.n), "-r", str(cell.ramp),
        "--run-time", f"{cell.run_time_s}s",
        "--csv", out_prefix, "--csv-full-history",
        # 램프업+워밍업 컷: 실행 후 WARMUP_CUT_S 지점에 통계 리셋(별도 wrapper 권장).
    ]
    subprocess.run(cmd, check=True)


def run_cell(cell: Cell) -> None:
    """한 칸 실행: 재시작→프리웜→(gauge off·scrape 15s는 스택 env)→부하→수집."""
    _restart_serving(cell.cores)
    _prewarm()
    prefix = f"loadtest/results/n{cell.n}_c{cell.cores}"
    _run_locust(cell, prefix)
    if cell.is_sm3:
        _trigger_reload_midload()           # SM-3: 부하 중 reload → OOM/메모리 관측(별도)


def _trigger_reload_midload() -> None:
    """SM-3 (결정 7): 부하 지속 중 /admin/reload → 순간 증분 메모리 관측.

    판정 = OOM/mem_limit 수용(누적분 baseline 제외 후 순간 증분). latency 는 별도 창.
    """
    req = urllib.request.Request(f"{SERVE_URL}/admin/reload", method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        r.read()


if __name__ == "__main__":
    for cell in cross_matrix():
        run_cell(cell)
