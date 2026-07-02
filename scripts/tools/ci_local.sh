#!/usr/bin/env bash
# ci_local.sh — CI를 GitHub가 아니라 로컬에서 돈다.
#
# .github/workflows/ci.yml 의 5개 job(test·lint·frontend·mutation·manifests)과 동일한
# 검사를 로컬에서 실행한다. push 전 pre-push 훅(scripts/hooks/pre-push)이 이걸 호출하며,
# 하나라도 실패하면 exit 1 → push 가 막힌다. GitHub Actions 는 workflow_dispatch 수동 백스톱.
#
# 직접 실행:   ./scripts/tools/ci_local.sh
# 환경변수:
#   CI_STRICT=1      로컬에 kubeconform/hadolint 가 없으면 skip 대신 실패 처리
#   SKIP_FRONTEND=1  프론트(console-web) 검사 생략 (node/npm 문제 임시 우회용)
#
# 주의: set -e 는 쓰지 않는다 — 한 job 이 실패해도 나머지를 마저 돌려 전체 결과를 한 번에 본다.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

# ── 출력 헬퍼 (tty 일 때만 색) ────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

declare -a PASSED=() FAILED=() SKIPPED=()

header() { printf '\n%s══ %s ══%s\n' "$BOLD" "$1" "$RESET"; }
pass()   { PASSED+=("$1");  printf '%s✔ %s%s\n' "$GREEN" "$1" "$RESET"; }
fail()   { FAILED+=("$1");  printf '%s✘ %s%s\n' "$RED" "$1" "$RESET"; }
skip()   { SKIPPED+=("$1"); printf '%s⤼ %s (skipped)%s\n' "$YELLOW" "$1" "$RESET"; }

# run <label> <cmd...> — 명령을 돌리고 결과를 기록. 실패해도 스크립트는 계속.
run() {
  local label="$1"; shift
  if "$@"; then pass "$label"; else fail "$label"; fi
}

# linux_node — PATH 의 node 가 WSL 네이티브(리눅스)인지. Windows nodejs(/mnt/c/...)는
# WSL 경로에서 CMD 가 UNC 를 거부해 못 돈다 → 그 경우는 프론트 검사를 skip 한다.
linux_node() {
  local n; n="$(command -v node 2>/dev/null)" || return 1
  [ -n "$n" ] || return 1
  case "$n" in /mnt/*) return 1 ;; esac
  return 0
}

# ── 1. test — pytest ─────────────────────────────────────────────────────────
# hermetic 스위트(모델/데이터 캐시 불필요)는 항상 돈다. serve/bench 는 학습된 XGB 모델
# 아티팩트가 있을 때만 — 없는데 돌리면 오프라인에서 무조건 실패하기 때문(자동 감지→skip).
# 커버리지(--cov·fail_under)는 CI 관심사라 로컬 게이트에선 빼고, 품질 바는 mutation 이 담당한다.
HERMETIC=(tests/console tests/console_prep tests/replay tests/data tests/drift tests/eval tests/retrain)
header "test — pytest (hermetic)"
if run "deps: uv sync --frozen" uv sync --frozen; then
  run "pytest (hermetic 185)" uv run pytest -q "${HERMETIC[@]}"
else
  fail "pytest (deps 동기화 실패로 미실행)"
fi

# serve/bench — 학습된 모델 아티팩트(mlruns 의 xgboost_*.ubj)가 있을 때만 포함.
if [ -n "$(find mlruns -name 'xgboost_*.ubj' -print -quit 2>/dev/null)" ]; then
  run "pytest (serve/bench, 아티팩트 감지)" uv run pytest -q tests/serve tests/bench
else
  skip "serve/bench (학습 모델 아티팩트 없음 — 학습 후 자동 포함)"
fi

# ── 2. lint — ruff ───────────────────────────────────────────────────────────
header "lint — ruff"
run "ruff check" uv run ruff check .

# ── 3. mutation — kill-rate 하네스 (survivor => fail) ─────────────────────────
header "mutation — kill-rate"
run "mutation harness" uv run python scripts/tools/mutation_test.py

# ── 4. frontend — console-web (eslint + typecheck/build + vitest) ────────────
header "frontend — console-web"
if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
  skip "frontend (SKIP_FRONTEND=1)"
elif ! linux_node; then
  # Windows nodejs(node.exe)만 있으면 WSL 경로에서 못 돈다 → WSL 네이티브 node 필요.
  if [ "${CI_STRICT:-0}" = "1" ]; then fail "frontend (linux node 없음, CI_STRICT)"; else skip "frontend (WSL 네이티브 node 없음 — 설치 시 활성화)"; fi
else
  # node_modules 가 없을 때만 lockfile 설치 (CI 는 매번 npm ci; 로컬은 재사용해 빠르게).
  if [ ! -d console-web/node_modules ]; then
    run "npm ci" bash -c 'cd console-web && npm ci'
  fi
  run "eslint"          bash -c 'cd console-web && npm run lint'
  run "typecheck+build" bash -c 'cd console-web && npm run build'
  run "vitest"          bash -c 'cd console-web && npm test'
fi

# ── 5. manifests — k8s (kubeconform) + Dockerfiles (hadolint) ────────────────
header "manifests — k8s + Dockerfiles"
if command -v kubeconform >/dev/null 2>&1; then
  run "kubeconform (deploy/k8s)" kubeconform -strict -ignore-missing-schemas -summary deploy/k8s
elif [ "${CI_STRICT:-0}" = "1" ]; then
  fail "kubeconform (미설치, CI_STRICT)"
else
  skip "kubeconform (미설치 — 매니페스트 변경 시 GitHub Actions 수동 실행 권장)"
fi

if command -v hadolint >/dev/null 2>&1; then
  dockerfiles=(deploy/Dockerfile deploy/k8s/console/Dockerfile.api console-web/Dockerfile)
  hadolint_ok=1
  for df in "${dockerfiles[@]}"; do
    printf '%s== %s ==%s\n' "$DIM" "$df" "$RESET"
    hadolint --failure-threshold error "$df" || hadolint_ok=0
  done
  if [ "$hadolint_ok" = "1" ]; then pass "hadolint (Dockerfiles)"; else fail "hadolint (Dockerfiles)"; fi
elif [ "${CI_STRICT:-0}" = "1" ]; then
  fail "hadolint (미설치, CI_STRICT)"
else
  skip "hadolint (미설치 — Dockerfile 변경 시 GitHub Actions 수동 실행 권장)"
fi

# ── 요약 ─────────────────────────────────────────────────────────────────────
header "summary"
printf '%s통과 %d%s · %s실패 %d%s · %sskip %d%s\n' \
  "$GREEN" "${#PASSED[@]}" "$RESET" "$RED" "${#FAILED[@]}" "$RESET" "$YELLOW" "${#SKIPPED[@]}" "$RESET"
if [ "${#SKIPPED[@]}" -gt 0 ]; then
  printf '%sskip: %s%s\n' "$YELLOW" "${SKIPPED[*]}" "$RESET"
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
  printf '%s실패: %s%s\n' "$RED" "${FAILED[*]}" "$RESET"
  printf '%spush 중단됨. 위 실패를 고치고 다시 시도하세요 (긴급 우회: git push --no-verify).%s\n' "$BOLD" "$RESET"
  exit 1
fi
printf '%s모든 게이트 통과 — push 진행.%s\n' "$GREEN" "$RESET"
