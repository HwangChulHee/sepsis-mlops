"""run_matrix — N×코어 실험 매트릭스 오케스트레이션 (부하테스트 (나)).

설계: docs/design/load-test/ 결정 4·5·6·7 / 핸드오프 §2.4·§2.5.
**스크립트(TDD 대상 아님)** — 사람이 (가) 스택 up 후 실행한다. 실제 측정 수치는 SM.
실측 검증: N축은 `loadtest/results/sweep.sh`와 동치, SM-3는 수동 실행과 동치
(docs/reports/load_test_results.md §5). 이 스크립트는 그 둘을 파이썬으로 통합·정합.

칸(cell)마다:
  1. 서버 재시작 + 코어/스레드캡 동반 주입(compose override) + 프리웜 (M-r2-1·m-r3-1·결정 5).
  2. 측정 오염 방지 (결정 4) — per-patient gauge off(env 미설정), 램프 후 --reset-stats 워밍업 컷.
  3. Locust headless 부하 → RPS·p50/95/99·에러율 수집(결정 6).
  4. SM-3 칸: **부하 지속 중(백그라운드) reload 동시 트리거** → docker stats 메모리·OOM 관측(결정 7).

매트릭스: N축 1·10·50·200·500·1000 (코어2) / 코어축 1·2·4 (N200) + SM-3 칸(N200).
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

# ── 매트릭스 정의 (결정 5) ──────────────────────────────────────────────────
N_AXIS = [1, 10, 50, 200, 500, 1000]      # 코어 2 고정, 무릎까지 연장
CORE_AXIS = [1, 2, 4]                       # N=200 고정, 스레드캡 동반
REP_CORE = 2                                # N축 대표 코어(실배포값)
REP_N = 200                                 # 코어축·SM-3 대표 N
WARMUP_CUT_S = 30                           # 램프업 후 컷(참고 — 실컷은 --reset-stats로)
RESULTS = Path("loadtest/results")
COMPOSE = ["docker", "compose", "-f", "deploy/docker-compose.yml", "--project-directory", "."]
SERVE_URL = "http://localhost:8000"
SERVING_CTR = "sepsis-serving"


@dataclasses.dataclass
class Cell:
    n: int
    cores: int
    run_time_s: int = 60
    ramp: int = 200
    is_sm3: bool = False   # SM-3(부하 중 reload) 칸 여부


def cross_matrix() -> list[Cell]:
    """십자 매트릭스 + SM-3 칸. (코어2·N200 중복 제거)."""
    cells: list[Cell] = [Cell(n=n, cores=REP_CORE, ramp=min(n, 200)) for n in N_AXIS]
    cells += [Cell(n=REP_N, cores=c) for c in CORE_AXIS if c != REP_CORE]
    cells.append(Cell(n=REP_N, cores=REP_CORE, is_sm3=True))   # SM-3(결정 7)
    return cells


# ── 서버 재시작 + 코어/스레드캡 주입 (M-r2-1·결정 5) ─────────────────────────

def _core_override(cores: int) -> str:
    """cpus 와 BLAS 스레드캡을 cores 동수로 맞추는 compose override YAML 을 tmp 에 쓴다.

    결정 5: 코어를 바꿀 때 `*_NUM_THREADS`도 반드시 동수(두 변수 혼입 방지).
    override 는 base compose 위에 병합(-f base -f override) — cpus 는 대체, env 는 추가.
    """
    body = (
        "services:\n"
        "  serving:\n"
        f"    cpus: {cores}\n"
        "    environment:\n"
        f'      OMP_NUM_THREADS: "{cores}"\n'
        f'      OPENBLAS_NUM_THREADS: "{cores}"\n'
        f'      MKL_NUM_THREADS: "{cores}"\n'
        f'      NUMEXPR_NUM_THREADS: "{cores}"\n'
    )
    fd = tempfile.NamedTemporaryFile("w", suffix=".override.yml", delete=False)
    fd.write(body)
    fd.close()
    return fd.name


def _restart_serving(cores: int) -> None:
    """코어/스레드캡을 cores 로 주입해 serving 을 재생성하고 healthy 될 때까지 대기.

    재생성(up -d --force-recreate)으로 서버 pid 상태(_h·_locks·_last)도 리셋(M-r2-1).
    thread cap 은 프로세스 시작 시 읽히므로 restart 가 아니라 **재생성**이 필요하다.
    """
    override = _core_override(cores)
    subprocess.run(
        COMPOSE + ["-f", override, "up", "-d", "--force-recreate", "serving"],
        check=True,
    )
    _wait_healthy()


def _wait_healthy(timeout_s: int = 600) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", SERVING_CTR],
            capture_output=True, text=True,
        )
        if out.stdout.strip() == "healthy":
            return
        time.sleep(2)
    raise RuntimeError("serving 이 제한시간 안에 healthy 되지 않음")


def _prewarm() -> None:
    """프리웜 1건 (m-r3-1) — 첫 /predict lazy-boot(300-trial 캘리브레이션)를 부하 밖에서 소진."""
    payload = {"patient_id": "warmup", "features": {
        "HR": 88, "O2Sat": 97, "Temp": 37, "SBP": 120, "MAP": 80,
        "DBP": 66, "Resp": 18, "Age": 64, "Gender": 1}}
    req = urllib.request.Request(
        f"{SERVE_URL}/predict", method="POST",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


# ── 부하 실행 ────────────────────────────────────────────────────────────────

def _locust_cmd(cell: Cell, prefix: str) -> list[str]:
    return [
        "locust", "-f", "loadtest/locustfile.py",
        "--host", SERVE_URL, "--headless",
        "-u", str(cell.n), "-r", str(cell.ramp),
        "--run-time", f"{cell.run_time_s}s",
        "--reset-stats",              # 램프업 완료 시점 통계 리셋 = 워밍업 컷(M2)
        "--csv", prefix, "--csv-full-history",
    ]


def _mem_usage_mib() -> float | None:
    """serving 컨테이너 현재 메모리(MiB). 실패 시 None."""
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", SERVING_CTR],
        capture_output=True, text=True)
    tok = out.stdout.strip().split("/")[0].strip()  # "265MiB / 2GiB" -> "265MiB"
    if tok.endswith("MiB"):
        return float(tok[:-3])
    if tok.endswith("GiB"):
        return float(tok[:-3]) * 1024
    return None


def _oom_killed() -> bool:
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.OOMKilled}}", SERVING_CTR],
        capture_output=True, text=True)
    return out.stdout.strip() == "true"


def run_cell(cell: Cell) -> dict:
    """한 칸 실행: 재시작(코어 주입)→프리웜→부하(→SM-3면 부하 중 reload)→결과."""
    _restart_serving(cell.cores)
    _prewarm()
    tag = "sm3" if cell.is_sm3 else f"n{cell.n}_c{cell.cores}"
    prefix = str(RESULTS / tag)
    cmd = _locust_cmd(cell, prefix)

    if not cell.is_sm3:
        subprocess.run(cmd, check=True)
        return {"cell": tag, "mem_end_MiB": _mem_usage_mib(), "oom": _oom_killed()}

    # SM-3: 부하를 백그라운드로 띄우고, 중간 지점에 reload 를 동시 트리거하며 메모리 샘플링.
    proc = subprocess.Popen(cmd)
    peak = 0.0
    reload_done = False
    for t in range(cell.run_time_s):
        m = _mem_usage_mib()
        if m:
            peak = max(peak, m)
        if t == cell.run_time_s // 2 and not reload_done:
            _trigger_reload()          # ★부하 지속 중(동시) reload — idle 아님
            reload_done = True
        if proc.poll() is not None:
            break
        time.sleep(1)
    proc.wait()
    return {"cell": tag, "peak_MiB": round(peak, 1), "oom": _oom_killed(),
            "verdict": "PASS(OOM 없음)" if not _oom_killed() else "FAIL(OOM-kill)"}


def _trigger_reload() -> None:
    """SM-3 (결정 7): /admin/reload. 판정 = OOM/mem_limit 수용(순간 증분). latency 별도."""
    req = urllib.request.Request(f"{SERVE_URL}/admin/reload", method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        r.read()


if __name__ == "__main__":
    RESULTS.mkdir(parents=True, exist_ok=True)
    summary = [run_cell(cell) for cell in cross_matrix()]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
