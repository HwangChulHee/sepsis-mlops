"""On-Prem Compose 설정 계약 TDD RED (핸드오프 §3.2 CG-1~CG-10).

대상 산출물(아직 없음 → RED):
  - deploy/docker-compose.yml (신규 통합 스택 — 기존 deploy/monitoring/docker-compose.yml 과 다른 경로)
  - deploy/monitoring/prometheus.yml (타깃 수정)
출처(이 문서만 신뢰): docs/design/onprem-compose/handoff.md §2.2·§2.4·§3.2.
**src/·기존 deploy 구현 코드는 읽지 않았다** — 핸드오프가 처방한 정적 파싱 계약만 검증한다.

각 CG 는 독립 테스트 함수. 파일 부재/규칙 위반 → RED. 정적 YAML 파싱은 문자열(포트·경로)만
본다 — 바이너리 실재·200 응답은 SM(런타임)의 몫이라 여기서 검증하지 않는다(§3.2 한계).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"
PROMETHEUS = REPO_ROOT / "deploy" / "monitoring" / "prometheus.yml"


# ---------------------------------------------------------------- 로더/헬퍼

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        pytest.fail(f"대상 파일 미존재(RED 정상): {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        pytest.fail(f"{path} 최상위가 매핑이 아님: {type(data)}")
    return data


def _services(compose: dict) -> dict:
    svc = compose.get("services")
    if not isinstance(svc, dict):
        pytest.fail("compose 에 services 매핑이 없음")
    return svc


def _service(compose: dict, name: str) -> dict:
    svc = _services(compose).get(name)
    if not isinstance(svc, dict):
        pytest.fail(f"서비스 '{name}' 정의가 없음(핸드오프 §2.2 처방)")
    return svc


def _env_dict(service: dict) -> dict[str, str]:
    """environment 를 dict/list 양쪽 형태에서 {KEY: val} 로 정규화."""
    env = service.get("environment")
    out: dict[str, str] = {}
    if isinstance(env, dict):
        for k, v in env.items():
            out[str(k)] = "" if v is None else str(v)
    elif isinstance(env, list):
        for item in env:
            s = str(item)
            if "=" in s:
                k, v = s.split("=", 1)
                out[k] = v
            else:
                out[s] = ""
    return out


def _healthcheck_test_str(service: dict) -> str:
    """healthcheck.test 를 하나의 문자열로 평탄화(list/str 양쪽 형태)."""
    hc = service.get("healthcheck")
    if not isinstance(hc, dict):
        return ""
    test = hc.get("test")
    if isinstance(test, list):
        return " ".join(str(x) for x in test)
    if test is None:
        return ""
    return str(test)


def _norm_bytes(value) -> int | None:
    """compose mem_limit 문자열/정수를 바이트로 정규화. k8s식(Gi/Mi 등)은 무효 → None.

    docker RAMInBytes: 접미사 b/k/m/g(및 kb/mb/gb), 1024 기반. 순수 정수=바이트.
    """
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]*)", s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2).lower()
    factors = {
        "": 1, "b": 1,
        "k": 1024, "kb": 1024,
        "m": 1024 ** 2, "mb": 1024 ** 2,
        "g": 1024 ** 3, "gb": 1024 ** 3,
    }
    if suffix not in factors:  # 'gi'/'mi'/'ki' 등 k8s 문법 = compose 무효
        return None
    return int(num * factors[suffix])


def _depends_conditions(service: dict) -> dict[str, str]:
    """depends_on(dict 형태)에서 {대상서비스: condition} 추출. list 형태는 condition 부재."""
    dep = service.get("depends_on")
    out: dict[str, str] = {}
    if isinstance(dep, dict):
        for target, spec in dep.items():
            if isinstance(spec, dict):
                out[str(target)] = str(spec.get("condition", ""))
            else:
                out[str(target)] = ""
    elif isinstance(dep, list):
        for target in dep:
            out[str(target)] = ""
    return out


ONEGI = 1073741824  # 1 Gi = 1024^3 bytes


# ------------------------------------------------------------------- CG-1

def test_cg1_no_deploy_resources_and_serving_toplevel_limits():
    """CG-1: 어떤 서비스도 deploy.resources 금지 + serving 최상위 cpus·mem_limit(≥1Gi 바이트).

    mem_limit 은 compose 단위 문자열을 바이트로 정규화 후 비교(문자 '1Gi' 비교 금지, m6).
    """
    compose = _load_yaml(COMPOSE)
    services = _services(compose)

    for name, svc in services.items():
        deploy = svc.get("deploy")
        if isinstance(deploy, dict):
            assert "resources" not in deploy, (
                f"서비스 '{name}' 가 deploy.resources 를 가짐(compose v1 에서 무시됨, 금지)"
            )

    serving = _service(compose, "serving")
    assert "cpus" in serving, "serving 에 최상위 cpus 가 없음(결정 9)"
    assert "mem_limit" in serving, "serving 에 최상위 mem_limit 가 없음(결정 9)"

    normalized = _norm_bytes(serving["mem_limit"])
    assert normalized is not None, (
        f"mem_limit={serving['mem_limit']!r} 가 compose 유효 단위가 아님"
        " (k8s식 '1Gi' 등은 무효 — '1g'/'2g'/바이트 정수 사용)"
    )
    assert normalized >= ONEGI, (
        f"mem_limit 이 1Gi(={ONEGI}B) 미만: {serving['mem_limit']!r} -> {normalized}B"
    )


# ------------------------------------------------------------------- CG-2

def test_cg2_service_healthy_targets_all_define_healthcheck():
    """CG-2: condition service_healthy 로 참조되는 모든 서비스는 자신의 healthcheck 보유(전수).

    루프 최대 교훈(healthcheck-게이트 함정, R1~R3)을 규칙 단위 전수 계약으로 봉인.
    """
    compose = _load_yaml(COMPOSE)
    services = _services(compose)

    violations = []
    for name, svc in services.items():
        for target, condition in _depends_conditions(svc).items():
            if condition == "service_healthy":
                target_svc = services.get(target)
                if not isinstance(target_svc, dict) or "healthcheck" not in target_svc:
                    violations.append(f"{name} -> {target}(service_healthy)")

    assert not violations, (
        "service_healthy 로 게이트되는데 healthcheck 미정의: " + ", ".join(violations)
    )


# ------------------------------------------------------------------- CG-3

def test_cg3_console_api_serve_url_is_service_dns():
    """CG-3: console-api env SERVE_URL=http://serving:8000 (localhost 기본 폴백 금지, 결정 3)."""
    compose = _load_yaml(COMPOSE)
    env = _env_dict(_service(compose, "console-api"))
    assert env.get("SERVE_URL") == "http://serving:8000", (
        f"SERVE_URL 이 http://serving:8000 이어야 함. got={env.get('SERVE_URL')!r}"
    )


# ------------------------------------------------------------------- CG-4

def test_cg4_featuresets_are_vitals():
    """CG-4: SERVE_FEATURESET·CONSOLE_FEATURESETS 모두 vitals (B-R0-4)."""
    compose = _load_yaml(COMPOSE)
    services = _services(compose)

    merged: dict[str, str] = {}
    for svc in services.values():
        merged.update(_env_dict(svc))

    assert "SERVE_FEATURESET" in merged, "SERVE_FEATURESET env 가 어디에도 없음"
    assert "CONSOLE_FEATURESETS" in merged, "CONSOLE_FEATURESETS env 가 어디에도 없음"
    assert merged["SERVE_FEATURESET"] == "vitals", (
        f"SERVE_FEATURESET 은 vitals 여야 함. got={merged['SERVE_FEATURESET']!r}"
    )
    assert merged["CONSOLE_FEATURESETS"] == "vitals", (
        f"CONSOLE_FEATURESETS 은 vitals 여야 함. got={merged['CONSOLE_FEATURESETS']!r}"
    )


# ------------------------------------------------------------------- CG-5

def test_cg5_console_api_healthcheck_endpoint():
    """CG-5: console-api healthcheck 가 /console/versions?fs=vitals 사용(·/health 아님)."""
    compose = _load_yaml(COMPOSE)
    hc = _healthcheck_test_str(_service(compose, "console-api"))
    assert "/console/versions?fs=vitals" in hc, (
        f"console-api healthcheck 가 /console/versions?fs=vitals 를 쳐야 함. got={hc!r}"
    )
    assert "/health" not in hc, (
        f"console-api healthcheck 가 /health(serving 용)를 치면 안 됨. got={hc!r}"
    )


def test_cg5_serving_healthcheck_endpoint_and_port():
    """CG-5: serving healthcheck 가 /health·포트 8000 정합."""
    compose = _load_yaml(COMPOSE)
    hc = _healthcheck_test_str(_service(compose, "serving"))
    assert "/health" in hc, f"serving healthcheck 가 /health 를 쳐야 함. got={hc!r}"
    assert "8000" in hc, f"serving healthcheck 가 포트 8000 을 쳐야 함. got={hc!r}"


def test_cg5_console_web_healthcheck_endpoint_and_port():
    """CG-5: console-web healthcheck 가 포트 8080·엔드포인트 / 정합(B3-1)."""
    compose = _load_yaml(COMPOSE)
    hc = _healthcheck_test_str(_service(compose, "console-web"))
    assert "8080" in hc, f"console-web healthcheck 가 포트 8080 을 쳐야 함. got={hc!r}"
    assert "/" in hc, f"console-web healthcheck 가 엔드포인트 / 를 쳐야 함. got={hc!r}"


def test_cg5_front_nginx_healthcheck_endpoint_and_port():
    """CG-5: front-nginx healthcheck 가 엔드포인트 /·포트 80 정합(m7 — / 는 준공허라 80 이 실가드)."""
    compose = _load_yaml(COMPOSE)
    hc = _healthcheck_test_str(_service(compose, "front-nginx"))
    assert "/" in hc, f"front-nginx healthcheck 가 엔드포인트 / 를 쳐야 함. got={hc!r}"
    assert "80" in hc, f"front-nginx healthcheck 가 포트 80 을 쳐야 함(m7). got={hc!r}"


# ------------------------------------------------------------------- CG-6

def test_cg6_prometheus_target_is_service_dns():
    """CG-6: prometheus 타깃 = serving:8000 (host.docker.internal 금지, 결정 4)."""
    prom = _load_yaml(PROMETHEUS)
    scrape = prom.get("scrape_configs")
    assert isinstance(scrape, list) and scrape, "prometheus.yml 에 scrape_configs 가 없음"

    all_targets: list[str] = []
    for job in scrape:
        for sc in job.get("static_configs", []) or []:
            for t in sc.get("targets", []) or []:
                all_targets.append(str(t))

    assert "serving:8000" in all_targets, (
        f"prometheus 타깃에 serving:8000 이 있어야 함. got={all_targets!r}"
    )
    assert not any("host.docker.internal" in t for t in all_targets), (
        f"prometheus 타깃에 host.docker.internal 이 있으면 안 됨. got={all_targets!r}"
    )


# ------------------------------------------------------------------- CG-7

def test_cg7_console_api_depends_serving_not_service_healthy():
    """CG-7: console-api→serving 의존이 service_healthy 아님(service_started 또는 부재, M-2)."""
    compose = _load_yaml(COMPOSE)
    conditions = _depends_conditions(_service(compose, "console-api"))
    serving_cond = conditions.get("serving", "")
    assert serving_cond != "service_healthy", (
        "console-api 가 serving 을 service_healthy 로 게이트하면 안 됨"
        " (캘리브레이션 300s·seed 누락 진단성). got=service_healthy"
    )


# ------------------------------------------------------------------- CG-8

def test_cg8_serving_restart_not_auto():
    """CG-8: serving restart 가 always/unless-stopped/on-failure 아님(=no 또는 부재, B1).

    이들 중 하나면 exit 3 에도 재시작 → crash-loop → §0(읽을 수 있는 실패) 붕괴.
    """
    compose = _load_yaml(COMPOSE)
    serving = _service(compose, "serving")
    restart = serving.get("restart", "no")
    restart = str(restart).strip().strip('"').strip("'")
    assert restart not in {"always", "unless-stopped", "on-failure"}, (
        f"serving restart 가 crash-loop 유발 정책이면 안 됨. got={restart!r}"
    )


# ------------------------------------------------------------------- CG-9

def test_cg9_serving_and_console_api_run_as_uid_10001():
    """CG-9: serving·console-api user 첫 필드 uid==10001(비-root, B2-R2).

    root 로 돌면 §2.5 chown 10001 이 죽은 처방 → 번들 원자성·감사 append-only 가드 붕괴.
    """
    compose = _load_yaml(COMPOSE)
    for name in ("serving", "console-api"):
        svc = _service(compose, name)
        user = svc.get("user")
        assert user is not None, f"서비스 '{name}' 에 user: 가 명시돼야 함(기본 root 방지)"
        uid = str(user).strip().strip('"').strip("'").split(":", 1)[0]
        assert uid == "10001", (
            f"서비스 '{name}' user uid 가 10001 이어야 함. got={user!r}"
        )


# ------------------------------------------------------------------- CG-10

def test_cg10_console_api_audit_db_url_points_to_auditdb_volume():
    """CG-10: console-api env CONSOLE_AUDIT_DB_URL 존재·sqlite:////app/auditdb/ 지향(R3 major).

    부재 시 /app/var 기본 폴백 → uid 10001 이 root 소유 /app 하위 mkdir 불가 → 부팅 크래시.
    CG-3(SERVE_URL localhost 금지)와 동일 클래스의 env-폴백 가드.
    """
    compose = _load_yaml(COMPOSE)
    env = _env_dict(_service(compose, "console-api"))
    assert "CONSOLE_AUDIT_DB_URL" in env, (
        "console-api env 에 CONSOLE_AUDIT_DB_URL 이 있어야 함(/app/var 폴백 방지)"
    )
    url = env["CONSOLE_AUDIT_DB_URL"]
    assert url.startswith("sqlite:////app/auditdb/"), (
        f"CONSOLE_AUDIT_DB_URL 이 sqlite:////app/auditdb/ 를 가리켜야 함. got={url!r}"
    )
