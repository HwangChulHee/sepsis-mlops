"""serving entrypoint seed-precondition TDD RED (핸드오프 §3.1 / §2.1).

대상 산출물(아직 없음 → RED): deploy/serving-entrypoint.sh
출처(이 문서만 신뢰): docs/design/onprem-compose/handoff.md §2.1·§3.1.
**src/·기존 deploy 구현 코드는 읽지 않았다** — 핸드오프가 처방한 관측가능 행동만 검증한다.

핸드오프가 못 박은 계약(신뢰 근거):
- §2.1 로직: `[ ! -e "$ARTIFACTS_DIR/gru_$FS" ]` 이면
    stderr에 "FATAL: active alias 'gru_$FS' missing ..." + "seed first ..." 출력 후 `exit 3`.
    (FS 기본값 = vitals → alias 이름 `gru_vitals`)
- alias 존재 시 `exec uvicorn sepsis.serve.app:app --host 0.0.0.0 --port 8000 ...` 도달.
- §2.1 Dockerfile ENTRYPOINT = `["/bin/sh","/app/deploy/serving-entrypoint.sh"]`
    → 테스트도 `sh <script>` 경유 호출(파이썬 로직 아님).
- §3.1 번역 괴리 주의: 실제 uvicorn/torch를 띄우지 않는다 — PATH 앞에 가짜 uvicorn
    실행파일을 두어 exec 도달만 관측한다. (유닛 GREEN이 컨테이너 기동을 증명하진 않음, M1.)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "serving-entrypoint.sh"


def _run(env_extra: dict[str, str], path_prepend: str | None = None):
    """`sh <script>` 로 entrypoint 호출. 스크립트 부재면 즉시 RED 로 실패."""
    if not SCRIPT.exists():
        pytest.fail(f"entrypoint 스크립트 미존재(RED 정상): {SCRIPT}")
    env = dict(os.environ)
    # 실제 서버가 뜨지 않도록 uvicorn/torch 를 건드리지 않는 stub PATH 를 앞세운다.
    if path_prepend is not None:
        env["PATH"] = path_prepend + os.pathsep + env.get("PATH", "")
    env.update(env_extra)
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_missing_alias_exits_3_with_seed_message(tmp_path):
    """§2.1: gru_vitals alias 없는 ARTIFACTS_DIR → 종료코드 3 + stderr 'seed first'.

    핸드오프 §0/§2.1 지배 산출물(읽을 수 있는 실패)의 관측가능 기준.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()  # gru_vitals alias 를 일부러 만들지 않는다.

    proc = _run({"ARTIFACTS_DIR": str(artifacts), "SERVE_FEATURESET": "vitals"})

    assert proc.returncode == 3, (
        f"seed 미완 precondition 실패는 exit 3 이어야 함. got={proc.returncode}\n"
        f"stderr={proc.stderr!r}"
    )
    assert "seed first" in proc.stderr, (
        f"stderr 에 'seed first' 복구 안내가 있어야 함. stderr={proc.stderr!r}"
    )


def test_missing_alias_default_featureset_is_vitals(tmp_path):
    """§2.1: SERVE_FEATURESET 미지정이면 FS 기본 vitals → gru_vitals 부재로 exit 3.

    alias 이름이 `gru_<fs>` 이고 기본 fs=vitals 임을(결정 8) 관측.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    proc = _run({"ARTIFACTS_DIR": str(artifacts)})  # SERVE_FEATURESET 미지정

    assert proc.returncode == 3, (
        f"기본 fs=vitals 의 gru_vitals 부재 → exit 3. got={proc.returncode}\n"
        f"stderr={proc.stderr!r}"
    )
    assert "gru_vitals" in proc.stderr, (
        f"stderr 메시지에 기본 alias 이름 gru_vitals 가 나와야 함. stderr={proc.stderr!r}"
    )


def test_alias_present_reaches_uvicorn_exec(tmp_path):
    """§2.1: gru_vitals alias 존재 → uvicorn exec 도달.

    실제 uvicorn/torch 를 띄우지 않도록 PATH 앞에 가짜 uvicorn 실행파일을 둔다(§3.1 방식).
    가짜 uvicorn 이 호출됐음을 sentinel 파일로 관측한다.
    """
    artifacts = tmp_path / "artifacts"
    (artifacts / "gru_vitals").mkdir(parents=True)  # alias 존재

    stubdir = tmp_path / "stubbin"
    stubdir.mkdir()
    sentinel = tmp_path / "uvicorn_called.txt"
    fake_uvicorn = stubdir / "uvicorn"
    fake_uvicorn.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{sentinel}"\n'
        "exit 0\n"
    )
    fake_uvicorn.chmod(0o755)

    proc = _run(
        {"ARTIFACTS_DIR": str(artifacts), "SERVE_FEATURESET": "vitals"},
        path_prepend=str(stubdir),
    )

    assert proc.returncode != 3, (
        f"alias 존재 시 seed precondition(exit 3) 로 죽으면 안 됨. "
        f"got={proc.returncode}, stderr={proc.stderr!r}"
    )
    assert sentinel.exists(), (
        "alias 존재 시 uvicorn exec 에 도달해야 함(가짜 uvicorn 미호출 = 미도달). "
        f"stderr={proc.stderr!r}"
    )
    args = sentinel.read_text()
    assert "sepsis.serve.app:app" in args, (
        f"uvicorn 이 sepsis.serve.app:app 대상으로 호출돼야 함. got={args!r}"
    )
    assert "8000" in args, f"uvicorn 이 포트 8000 으로 기동돼야 함. got={args!r}"
