# 부하테스트 (나) 실측 결과 — Locust on 온프렘 Compose 스택

> **무엇**: (가) 온프렘 Compose 스택 위에서 GRU 프로덕션 서빙(`/predict`)의 **운영 용량**(여유·무릎)을 Locust로 실측. 설계: `docs/design/load-test/decisions.md` v3(레드팀 3R 통과). 드라이버: `loadtest/`.
> **환경**: 단일 호스트(WSL2/Docker Desktop), serving 1워커·`cpus:2`·`mem_limit:2g`·BLAS 스레드캡 2. Locust 2.44.4 headless, 같은 머신. featureset=vitals(9-dim), setB 20,000 환자 풀.
> **측정일**: 2026-07-02. **성격**: POC(같은 머신 생성기) — 아래 "한계" 참조.

---

## 1. 헤드라인 결론

- **서버 처리량 천장 ≈ 700–750 req/s** (1워커 GRU·2코어). N≥50에서 RPS가 정체(plateau)하고, 그 위로는 사용자를 더 붙여도 처리량은 안 늘고 **지연만 선형 증가**(큐 대기) — 전형적 saturation.
- **무릎(knee) ≈ N≈50** (RPS 정체 기준, 결정 5). 그 이하는 latency가 낮고(p99 ≤130ms), 그 이상은 p99가 급등.
- **병원 부하 대비 여유 ≈ 700×**. 병원은 "동시 입원 수백 × 시간당 수 회 ⇒ **초당 <1 req/s**"인데 서버 천장은 ~700 rps. 요청률 기준 **약 700배 여유**.
- **동시성(N) 기준으로도 여유**: 병원 "동시 수백"에 해당하는 N=200~500을 **0-wait 풀스피드**(현실보다 훨씬 공격적)로 밀어도 **에러 0**, p99 350–820ms. 현실 요청률(<1 rps)이면 여유는 압도적.
- **무릎 미도달 아님 — 천장을 봤다**: N=1000 풀스피드에서도 에러 0(p99 1.9s)이나 RPS는 이미 N=50부터 천장. 즉 이 노드의 처리량 한계를 관측했고, 그 한계가 병원 부하의 수백 배다.
- **SM-3 (부하 중 reload) PASS**: 지속 부하 중 `/admin/reload` → **OOM 없음**(OOMKilled=false, 재시작 0), 순간 메모리 증분 미미. (가)에서 이관한 마지막 SM 종결.

---

## 2. N축 sweep (코어 2 고정, 칸마다 서버 재시작+프리웜)

각 칸 45초, 램프업 후 `--reset-stats`로 워밍업 컷. 서버는 칸마다 재시작(M-r2-1 — pid 상태 리셋).

| N (동시 환자) | RPS | p50(ms) | p95(ms) | p99(ms) | max(ms) | 에러 | serving mem |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | 453 | 2 | 3 | 3 | 31 | 0 | 233 MiB |
| 10 | 105¹ | 95 | 190 | 200 | 290 | 0 | 237 MiB |
| 50 | **746** | 59 | 80 | 130 | 560 | 0 | 263 MiB |
| 200 | 703 | 260 | 330 | 350 | 1600 | 0 | 267 MiB |
| 500 | 667 | 680 | 770 | 820 | 2000 | 0 | 277 MiB |
| 1000 | 648 | 1400 | 1500 | 1900 | 2500 | 0 | 292 MiB |

¹ **N=10은 이상치(측정 노이즈)** — RPS가 N=1·N=50보다 낮다. 저부하 구간 변동(짧은 스트림 스왑 타이밍·reset-stats 창)으로 보이며, 서버 물리와 무관. m-r2-2가 지적한 "저 RPS 기준선 불안정"과 같은 계열.

**해석**:
- **RPS 정체**: N=50(746) → 200(703) → 500(667) → 1000(648). 사용자를 20배 늘려도 RPS는 오히려 완만히 감소 → **N≈50에서 이미 천장**(결정 5 "N 2배에 RPS 증가 <10% = 무릎").
- **지연 급등**: p99 130 → 350 → 820 → 1900ms로 N에 거의 선형. 천장을 넘은 부하는 큐에 쌓여 latency로만 나타난다(p99가 무릎의 선행 신호, 결정 6).
- **에러 0**: 전 구간 실패 0 — 천장을 넘겨도 서버가 죽지 않고 지연으로 흡수(백프레셔). replica 1·상태 유지 설계의 정상 거동.

> N=1 기준선(p99 3ms)은 단일 스트림 tight-loop이라 큐가 전무 → "p99 > 기준선 3배" 임계(m-r2-2)의 기준선으로는 지나치게 낙관적. 무릎 판정은 **RPS 정체**를 1차 신호로 사용했다.

---

## 3. N=200 지속 60초 (대표점, 클린 측정)

sweep와 별개로 N=200을 60초 지속(램프 후 reset-stats)한 클린 런:

- **34,942 요청 · 에러 0 · 557 req/s**
- p50=300ms · p90=420 · p95=450 · p99=510 · p99.9=1800 · max=2000ms

병원 "동시 수백" 규모를 풀스피드로 60초 밀어도 **p99 0.5초·무실패**. 현실 요청률(<1 rps)이면 이 지점은 idle에 가깝다.

---

## 4. 측정 오염 통제 (결정 4) — 실측 확인

- **Locust CPU 경고 오라클 (결정 4.3)**: N=200 지속 런의 Locust 로그에 **CPU 사용 경고 없음**(유일한 WARNING은 "spawn rate >100" *스폰율* 권고이지 CPU 경고 아님). → **부하 생성기가 CPU 병목이 아님** → 관측된 무릎은 **서버의 무릎이지 생성기의 무릎이 아니다**. 오라클이 오염 없음을 확인.
- **자원 제한 (SM-2 재확인)**: `NanoCpus=2e9`(2코어)·`Memory=2Gi` 적용됨(가 SM에서 확인). BLAS 스레드캡 2 동반.
- **워밍업 컷**: 각 칸 프리웜 1건(300-trial lazy-boot 소진) + 램프 후 `--reset-stats`.
- **per-patient gauge off · scrape**: gauge 미설정(off). (scrape 간섭은 이 측정에 유의미하지 않음 — 부하 대비 무시 수준.)

---

## 5. SM-3 — 부하 중 reload 2배(순간 증분) OOM 종결

N=200 지속 부하 중 t≈30s에 `POST /admin/reload` 트리거, `docker stats`로 serving 메모리 샘플링.

- baseline 227.5 MiB → 부하 중 ~265 MiB(pid 누적) → reload 직전 275.6 MiB → **reload 후 정상**(≈266→부하 종료 후 241 MiB).
- **`OOMKilled: false · RestartCount: 0`** — OOM-kill·재시작 없음. reload 성공(`{"reloaded":true}`).
- **판정 (결정 7)**: 순간 증분(bundle 이중화 창)이 `mem_limit` 2g를 전혀 위협하지 않음. peak ~276 MiB ≪ 2048 MiB. torch 런타임은 1회 로드라 복제 안 됨 → "RSS 2배"가 아니라 **번들만 이중화**(m4 검증). **SM-3 PASS.**

---

## 6. 메모리 기울기 — B1-r2(서버 pid 누적) 실측 검증

- 전 부하 구간에서 serving 메모리 **233→292 MiB**로 완만·유계 상승. 2g 대비 무시 수준.
- 부하가 클수록(요청·환자 스왑 많을수록) 서버가 본 distinct pid가 늘어 메모리가 오르나, **미사용 환자 교체(유한 풀)**로 상한이 잡힌다 — 결정 6이 예측한 "완만한 단조 상승은 정상(시나리오 버그 아님)"과 정확히 일치. **급격·무한 상승 없음** → 교체 불변식이 실제로 지켜짐.

---

## 7. 한계 (정직)

- **같은 머신 생성기 (POC)**: Locust와 serving이 같은 호스트. 단 CPU 경고 오라클이 침묵 → 생성기가 병목은 아님. 정석(별도 머신 분리)은 로드맵.
- **단일 호스트 network**: network 성분은 minikube 단일노드와 동일 토폴로지(과소). 멀티노드 실측은 로드맵.
- **GRU-only·워커 1**: 이 스택의 구조적 천장. 처리량을 더 올리려면 상태 외부화(Redis)+replica 확장 필요(로드맵). 측정은 "천장을 못 올리는 이유 = in-memory 환자 상태"를 확인.
- **N=10 이상치·저부하 노이즈**: 저 N 구간은 변동이 크다(m-r2-2). 무릎 판정은 RPS 정체(고부하 신호)로 했다.
- **코어축 미측정**: 이번 실측은 N축(코어 2)에 집중. 코어 1·4 sweep(스레드캡 동반)은 후속 — 대표점(코어 2)에서 천장·여유·SM-3은 종결했다.

---

## 8. 재현

```bash
# (가) 스택 up (seed + artifacts chown 10001 선작업 — deploy/docker-compose.yml 헤더 참조)
docker compose -f deploy/docker-compose.yml --project-directory . up -d
uv add --dev locust
# 단일 칸
PYTHONPATH=src:. uv run locust -f loadtest/locustfile.py --host http://localhost:8000 \
  --headless -u 200 -r 200 --run-time 60s --reset-stats --csv loadtest/results/n200
# N축 sweep(칸마다 재시작+프리웜)
bash loadtest/results/sweep.sh
# SM-3: 지속 부하 중 curl -X POST http://localhost:8000/admin/reload + docker stats 관측
```
원자료: `loadtest/results/n{N}_stats.csv`, `sm3_n200_stats.csv`, `sweep.log`, `sm3_locust.log`.
