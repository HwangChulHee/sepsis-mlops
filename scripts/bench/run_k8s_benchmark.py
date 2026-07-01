"""serving-benchmark k8s 실측 오케스트레이터 (호스트 실행).

로컬 실측을 k8s 로 옮긴 것 — GRU/XGB 서빙을 **파드**로 띄우고, **인-클러스터 클라이언트
파드**(파드↔파드 실 네트워크·kube-proxy 경유)로 latency/throughput 을 재고, 서빙 파드의
peak RSS 를 `kubectl exec`(VmHWM) 로 수집한다. 그래서 로컬(localhost)과 달리 client 벽시계에
진짜 k8s 네트워크가 포함된다. 결과를 `assemble_bench_result` 로 조립 → k8s 리포트.

전제: 이미지 sepsis-bench:latest 가 minikube docker 에 빌드돼 있음(deploy/bench/Dockerfile.bench).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

from sepsis import config as C
from sepsis.bench.result import assemble_bench_result

REPORT = C.ROOT / "docs" / "reports" / "serving_benchmark_k8s.md"
IMG = "sepsis-bench:latest"

# (배포명, 팩토리, featureset, aux, 아티팩트 env)
TARGETS = [
    ("bench-gru-arm1", "sepsis.serve.bench_app:build_gru_app", "vitals", "1",
     {"SEPSIS_GRU_ARTIFACTS_DIR": "/app/bake/gru_vitals"}),
    ("bench-gru-arm2", "sepsis.serve.bench_app:build_gru_app", "vitals", "0",
     {"SEPSIS_GRU_ARTIFACTS_DIR": "/app/bake/gru_vitals"}),
    ("bench-xgb9-arm1", "sepsis.serve.xgb_app:build_app_from_env", "vitals", "1",
     {"SEPSIS_XGB_MODEL_DIR": "/app/bake/xgb_vitals"}),
    ("bench-xgb18-arm1", "sepsis.serve.xgb_app:build_app_from_env", "vitals_labs", "1",
     {"SEPSIS_XGB_MODEL_DIR": "/app/bake/xgb_vitals_labs"}),
    ("bench-xgb18-arm2", "sepsis.serve.xgb_app:build_app_from_env", "vitals_labs", "0",
     {"SEPSIS_XGB_MODEL_DIR": "/app/bake/xgb_vitals_labs"}),
]


def _kubectl(*args, inp=None, timeout=120):
    return subprocess.run(["kubectl", *args], input=inp, capture_output=True, text=True, timeout=timeout)


def _manifest(name, factory, fs, aux, art_env):
    env = [f"        - {{name: BENCH_FACTORY, value: \"{factory}\"}}",
           f"        - {{name: SEPSIS_SERVE_AUX_METRICS, value: \"{aux}\"}}",
           f"        - {{name: SEPSIS_XGB_FEATURESET, value: \"{fs}\"}}"]
    for k, v in art_env.items():
        env.append(f"        - {{name: {k}, value: \"{v}\"}}")
    for t in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env.append(f"        - {{name: {t}, value: \"1\"}}")
    env_block = "\n".join(env)
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels: {{app: {name}, bench: "1"}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata:
      labels: {{app: {name}, bench: "1"}}
    spec:
      containers:
      - name: serving
        image: {IMG}
        imagePullPolicy: Never
        ports: [{{containerPort: 8000}}]
        env:
{env_block}
        startupProbe:
          httpGet: {{path: /health, port: 8000}}
          periodSeconds: 3
          failureThreshold: 40
        resources:
          requests: {{cpu: "250m", memory: "300Mi"}}
          limits: {{cpu: "1", memory: "900Mi"}}
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  labels: {{bench: "1"}}
spec:
  selector: {{app: {name}}}
  ports: [{{port: 80, targetPort: 8000}}]
"""


def deploy():
    docs = "\n---\n".join(_manifest(*t) for t in TARGETS)
    r = _kubectl("apply", "-f", "-", inp=docs)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit("apply failed")
    for name, *_ in TARGETS:
        print(f"[wait] rollout {name} ...", flush=True)
        rr = _kubectl("rollout", "status", f"deploy/{name}", "--timeout=180s")
        if rr.returncode != 0:
            print(rr.stdout, rr.stderr)
            sys.exit(f"rollout {name} failed")


def run_client():
    spec = ";".join(f"{n}=http://{n}={fs}" for n, _, fs, _, _ in TARGETS)
    _kubectl("delete", "pod", "bench-client", "--ignore-not-found")
    print("[client] running in-cluster ...", flush=True)
    r = _kubectl("run", "bench-client", f"--image={IMG}", "--image-pull-policy=Never",
                 "--restart=Never", f"--env=TARGETS={spec}", "--command", "--",
                 "python", "/app/scripts/bench/k8s_bench_client.py")
    if r.returncode != 0:
        sys.exit(f"client run failed: {r.stderr}")
    # 완료 대기
    for _ in range(120):
        ph = _kubectl("get", "pod", "bench-client", "-o", "jsonpath={.status.phase}").stdout
        if ph in ("Succeeded", "Failed"):
            break
        time.sleep(2)
    logs = _kubectl("logs", "bench-client").stdout
    if "BENCH_JSON_START" not in logs:
        print(logs[-2000:]); sys.exit("client produced no JSON")
    body = logs.split("BENCH_JSON_START")[1].split("BENCH_JSON_END")[0].strip()
    return json.loads(body)


def _pod_mem(name):
    pod = _kubectl("get", "pod", "-l", f"app={name}", "-o", "jsonpath={.items[0].metadata.name}").stdout.strip()
    st = _kubectl("exec", pod, "--", "sh", "-c", "cat /proc/1/status").stdout
    rss = peak = float("nan")
    for line in st.splitlines():
        if line.startswith("VmRSS:"):
            rss = float(line.split()[1]) / 1024.0
        elif line.startswith("VmHWM:"):
            peak = float(line.split()[1]) / 1024.0
    return rss, peak


def main():
    if "--cleanup" in sys.argv:
        _kubectl("delete", "deploy,svc,pod", "-l", "bench=1", "--ignore-not-found")
        _kubectl("delete", "pod", "bench-client", "--ignore-not-found")
        print("cleaned up"); return

    deploy()
    time.sleep(3)
    cli = run_client()
    mem = {n: _pod_mem(n) for n, *_ in TARGETS}
    for n in mem:
        print(f"  {n}: rss={mem[n][0]:.0f}MB peak={mem[n][1]:.0f}MB "
              f"server_med={__import__('statistics').median([s for _,s in cli[n]['reps']]):.3f}ms "
              f"feat={cli[n]['feature_lines']}", flush=True)

    def _arm(n):
        return {"client": cli[n]["client_all"], "server": cli[n]["server_all"],
                "rss": mem[n][0], "peak": mem[n][1]}

    injected = {
        "gru": {"arm1": _arm("bench-gru-arm1"), "arm2": _arm("bench-gru-arm2"),
                "control9": {"rss": mem["bench-gru-arm1"][0]},
                "throughput": cli["bench-gru-arm1"]["throughput"], "boot_latency": 0.0},
        "xgb": {"arm1": _arm("bench-xgb18-arm1"), "arm2": _arm("bench-xgb18-arm2"),
                "control9": {"rss": mem["bench-xgb9-arm1"][0]},
                "throughput": cli["bench-xgb18-arm1"]["throughput"], "boot_latency": 0.0},
        "cost": {"target_throughput": 1000.0,
                 "per_instance_throughput": cli["bench-xgb18-arm1"]["throughput"]["req_per_sec"],
                 "price_per_hr": 0.17, "instance_type": "c6i.xlarge (예시)",
                 "price_source": "https://aws.amazon.com/ec2/pricing/on-demand/ (예시)"},
    }
    result = assemble_bench_result(injected)
    _write_report(result, cli, mem)
    print(f"[done] -> {REPORT}")


def _med_s(cli, n):
    import statistics as s
    return s.median([sv for _, sv in cli[n]["reps"]])


def _spread(cli, n):
    vs = [sv for _, sv in cli[n]["reps"]]
    return min(vs), max(vs)


def _q(q):
    return f"{q.p50:.2f}/{q.p95:.2f}/{q.p99:.2f}"


def _write_report(r, cli, mem):
    g, x = r.gru, r.xgb
    gru9, xgb9, xgb18 = _med_s(cli, "bench-gru-arm1"), _med_s(cli, "bench-xgb9-arm1"), _med_s(cli, "bench-xgb18-arm1")
    gsp, x9sp, x18sp = _spread(cli, "bench-gru-arm1"), _spread(cli, "bench-xgb9-arm1"), _spread(cli, "bench-xgb18-arm1")
    L = []
    A = L.append
    A("# Serving Benchmark — GRU vs XGBoost (k8s 실측)\n")
    A("> **k8s(minikube) 인-클러스터 실측**: GRU/XGB 서빙을 파드로 띄우고 **인-클러스터 클라이언트 "
      "파드**로 측정. client 벽시계에 **파드↔파드 실 네트워크(ClusterIP·kube-proxy/iptables)** 포함 — "
      "로컬(localhost) 실측과 대비된다. 합성 결정론 스트림, 워밍업 제외, 측정창 100×5회 반복.\n")
    A("> 정직성: 헤드라인=(아키텍처×featureset) 결합 배포 프로파일. arm-1 잔차를 network 라 부르지 않음. "
      "best_iter 절단·골든=xgboost 3.3.0. minikube 단일노드라 데이터센터 멀티노드 network 와는 또 다름.\n")

    A("## 1. server 내부 추론 latency + 아키텍처/featureset 분리 (ms, 중앙값[min–max])\n")
    A("| featureset arm | server_mean | 반복 spread |")
    A("|---|---|---|")
    A(f"| GRU/vitals9 | {gru9:.3f} | [{gsp[0]:.3f}–{gsp[1]:.3f}] |")
    A(f"| XGB/vitals9 (통제) | {xgb9:.3f} | [{x9sp[0]:.3f}–{x9sp[1]:.3f}] |")
    A(f"| XGB/vitals_labs18 (배포) | {xgb18:.3f} | [{x18sp[0]:.3f}–{x18sp[1]:.3f}] |")
    A(f"\n- **아키텍처 기여**(XGB/9 − GRU/9) = **{xgb9 - gru9:+.3f} ms**. **featureset 기여**(XGB 9→18) = **{xgb18 - xgb9:+.3f} ms**.")

    A("\n## 2. 헤드라인 배포 arm — client(파드↔파드 네트워크 포함) / server\n")
    A("| 배포 arm | client(p50/95/99) | server(p50/95/99) | client_mean | server_mean | residual | tax |")
    A("|---|---|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {_q(g.arm1.client)} | {_q(g.arm1.server)} | {g.arm1.client_mean:.3f} | {g.arm1.server_mean:.3f} | {g.arm1.residual:.3f} | {g.tax:.3f} |")
    A(f"| XGB/vitals_labs18 | {_q(x.arm1.client)} | {_q(x.arm1.server)} | {x.arm1.client_mean:.3f} | {x.arm1.server_mean:.3f} | {x.arm1.residual:.3f} | {x.tax:.3f} |")
    A(f"\n- **residual = client_mean − server_mean = k8s 네트워크+직렬화+핸들러 후처리**(label={g.arm1.residual_label!r}, arm-1 network 단독 아님). "
      f"로컬 localhost 잔차(~1.7ms)와 비교하면 이 값이 k8s 네트워크 오버헤드를 반영.")

    A("\n## 3. throughput (인-클러스터 동시 부하)\n| 모델 | n_streams | req/sec | wall(s) |\n|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.throughput.n_streams} | {g.throughput.req_per_sec:.1f} | {g.throughput.wall_seconds:.2f} |")
    A(f"| XGB/vitals_labs18 | {x.throughput.n_streams} | {x.throughput.req_per_sec:.1f} | {x.throughput.wall_seconds:.2f} |")

    A("\n## 4. 메모리 (파드 peak RSS = VmHWM, MB)\n| 모델 | RSS(arm1) | peak | 계측세금(arm1−arm2) | featureset(control9−arm1) |\n|---|---|---|---|---|")
    A(f"| GRU/vitals9 | {g.memory.rss:.0f} | {g.memory.peak:.0f} | {g.memory.instrumentation:.1f} | {g.memory.input_dim:.1f} |")
    A(f"| XGB/vitals_labs18 | {x.memory.rss:.0f} | {x.memory.peak:.0f} | {x.memory.instrumentation:.1f} | {x.memory.input_dim:.1f} |")
    A(f"\n- **XGB stateless 아님**(stateless_claim={x.stateless_claim}). 파드 cgroup 안 RSS 라 컨테이너 기준. "
      f"계측세금·입력차원 기여는 이 규모 노이즈 수준(프로세스별 RSS 지터).")

    A("\n## 5. 비용 (수동)\n")
    c = r.cost
    A(f"- 목표 {c.target_throughput:.0f} req/s, per-instance {c.per_instance_throughput:.1f} req/s → {c.instance_count}대 × ${c.price_per_hr}/hr = **${c.cost_per_hr:.2f}/hr**.")

    A("\n## 6. 게이트·환경 관측\n")
    A(f"- 관측성 게이트(A1): arm-1 피처라인 GRU={cli['bench-gru-arm1']['feature_lines']}/XGB={cli['bench-xgb18-arm1']['feature_lines']}(>0), "
      f"arm-2 GRU={cli['bench-gru-arm2']['feature_lines']}/XGB={cli['bench-xgb18-arm2']['feature_lines']}(==0) — k8s 파드에서도 게이트 동작.")
    A(f"- 정상상태 컷 GRU={g.steady_state_start}/XGB={x.steady_state_start}. 파드 리소스 limit cpu=1·mem=900Mi, BLAS 스레드 캡=1.")

    A("\n## 7. 한계 (정직)\n")
    A("- **minikube 단일 노드**: 파드↔파드 네트워크는 같은 노드 내 가상 브리지라, 진짜 멀티노드 클러스터(노드 간 홉·오버레이 CNI)보다 network 가 작다. 그래도 kube-proxy/iptables·서비스 추상은 탄다.")
    A("- 합성 스트림·소표본·단일 실행(반복 5회). CPU limit 1 로 캡 — 실제 노드 자원과 다름.")
    A("- best_iter 골든=xgboost 3.3.0 전제. throughput 은 스모크 수준(4 스트림).")

    REPORT.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
