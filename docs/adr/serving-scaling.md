# 서빙 · 확장 아키텍처 설계 결정

**프로젝트:** sepsis-mlops (PhysioNet/CinC 2019 패혈증 조기예측)
**이 문서의 목적:** 모든 인프라 선택에 "규모 기반 근거"가 있음을 증명한다. 면접에서 받을 "왜 이렇게 했냐 / 왜 X는 안 썼냐" 압박 질문에 대한 답을 미리 정리한다.

> **핵심 원칙:** 도구를 안 쓴 건 "몰라서"가 아니라 "알고도 규모에 안 맞아서"다. 시니어가 보고 싶은 건 도구 자랑이 아니라 *시점 판단*이다.

---

## 출처 등급 범례

- `[확인됨]` — 1차 출처(공식 문서·논문) 또는 레포 코드로 검증된 사실
- `[우리 결정]` — 이 프로젝트의 설계 판단
- `[검증 필요]` — 부하 테스트 등으로 확정해야 할 추정

---

## 0. 한눈에 — 결정 요약

| 영역 | 결정 | 핵심 이유 |
|---|---|---|
| 실험 추적 | MLflow Tracking 그대로 | 표준이고 자체구현 이득 0 |
| 모델 버전 관리 | 자체 번들 (MLflow Registry ❌) | 번들-단위 원자 교체가 필요 (Registry는 모델-단위) |
| 서빙 포장 | 커스텀 번들 (pyfunc ❌) | pyfunc는 정적 짐만 묶음, hidden state는 못 가둠 |
| 서빙 프레임워크 | FastAPI 자체 (Triton ❌) | CPU 단일 노드엔 Triton이 오버킬 |
| 추론 하드웨어 | CPU (GPU ❌) | 작은 GRU 단일추론은 CPU가 오히려 유리 |
| 규모 산정 | 병원당 ≤500명 | 온프레미스 배포 → 노드당 모집단 한정 |
| 확장 방식 | K8s scale-out + Redis (경로) | stateful이라 상태 외부화 필요 |
| 상태 관리 패턴 | Redis 외부화 (Triton 라우팅 ❌) | CPU scale-out 맥락엔 외부화가 유리 |
| 장애 대비 | AOF→복제→Sentinel (경로) | 단일 Redis는 SPOF |

---

## 1. 실험 추적 — MLflow Tracking 그대로 쓴다

**결정:** 실험 추적·비교는 MLflow Tracking을 그대로 쓴다. 자체 구현하지 않는다.

**근거:**
- 6조합(XGB·LGBM·GRU × 2 featureset)이 이미 experiment `"h2"`에 run으로 로깅돼 있다 (`h2b_train_trees.py`, `h2c_train_gru.py`). `[확인됨, 레포 코드]`
- `mlflow ui`에서 6개 run을 Compare하면 parallel coordinates가 바로 나온다. PPT용 캡처로 끝. `[확인됨, MLflow 기능]`
- 실험 추적 화면을 자체 구현하는 건 W&B를 어설프게 재발명하는 것 — 이득 0. `[우리 결정]`

**한 줄:** "코드로 한 실험을 UI가 기록·비교한다. UI에서 실험을 *하는* 게 아니다. 그래서 표준 도구(MLflow Tracking)를 그대로 쓴다."

---

## 2. 모델 버전 관리 — MLflow Registry 대신 자체 번들

**검토한 대안:** MLflow Model Registry (버전·alias·승인·롤백)

**왜 안 썼나 — 관리 *단위*가 다르다:**
- MLflow Registry는 **"모델 1개"**를 관리한다. `[확인됨, MLflow 문서]`
- 우리 서빙은 **"번들 = 모델 + 전처리 + τ + drift reference 4개 묶음"**을 관리해야 한다. `[확인됨, 레포 `deploy.py`/`bundle.py`]`
- 롤백할 때: Registry는 모델 가중치만 되돌린다. 그러면 전처리·τ·reference가 짝이 안 맞아 train-serving skew가 터진다. 우리는 4개를 *한 단위로 함께* 되돌려야 한다 (`bundle.set_alias` + `os.replace` 원자 스왑).

**MLflow가 못 하는 것 (= 자체 구현한 부분):**

| 자체 구현 | MLflow Registry는? |
|---|---|
| 번들 4개 원자 스왑 | 모델 1개만 |
| drift reference 동반 롤백 | drift 개념 자체가 없음 |
| 도메인 검증 게이트 (B-holdout·무회귀·`cross_site_claim`) | "사람이 production 누름" 수준 |
| 승인 코드 강제 (`approved=False면 raise`) | 권한으로만 막음 |

**비유:** Registry는 "책 한 권"을 버전관리하는 도서관. 우리 서빙은 "책+책갈피+독서노트+번역본을 한 세트로" 다뤄야 한다. 책만 옛날 판으로 바꾸면 책갈피가 안 맞는다.

**한 줄:** "MLflow Registry를 검토했지만, 우리 서빙은 모델 단독이 아니라 전처리·임계값·드리프트 기준선이 한 번들로 원자 교체돼야 해서, 모델-단위 관리로는 부족해 번들-단위 배포를 직접 설계했습니다."

> **정직한 단서:** 번들이 "모델 하나"뿐이었으면 Registry로 충분했을 것. 4개 묶음(특히 reference 동반)이라 부족했던 것. `[우리 결정]`

---

## 3. 서빙 포장 — pyfunc 대신 커스텀 번들

**검토한 대안:** MLflow pyfunc (모델+전처리를 "데이터 넣으면 답 나오는" 한 덩어리로 포장)

**pyfunc로 묶을 수 있는 것 / 없는 것:**

| 번들 요소 | pyfunc에 묶기 | 실제로 어디 |
|---|---|---|
| 모델·전처리·τ·reference | 가능 (정적 짐) | 번들 |
| **hidden state** | **불가** (살아있는 상태) | scale-out 시 Redis |

**왜 hidden state는 못 묶나:**
- hidden state는 정적 짐이 아니라 매 요청마다 환자별로 갱신되고 요청 사이에 공유되는 *살아있는 상태*다.
- pyfunc의 `predict()` 표준 규약엔 이전 상태를 받는 인자가 없다. `[확인됨, MLflow 문서]`
- self에 딕셔너리로 숨길 순 있지만(클래스니까), MLflow 서빙 인프라가 그 상태를 pod 간에 공유·라우팅 안 해준다 → scale-out하면 깨진다. `[확인됨, 문서 + replicas 문제]`

**결론 논리:**
1. 정적 짐은 pyfunc로 묶을 수 있지만 —
2. hidden state는 어차피 Redis로 빠지고 —
3. 번들 단위 원자 롤백도 어차피 커스텀으로 짜야 하니 —
4. **"pyfunc + 커스텀" 두 겹보다 "커스텀 번들" 한 겹이 일관되고 단순하다.**

**한 줄:** "pyfunc는 정적 짐(모델·전처리·τ·reference)만 묶을 수 있고 hidden state는 못 가둡니다. 어차피 상태는 Redis로, 롤백은 커스텀으로 빠지니, 절반만 pyfunc로 싸느니 통째로 커스텀 번들로 묶는 게 단순합니다."

---

## 4. 서빙 프레임워크 — Triton 대신 FastAPI

**검토한 대안:** NVIDIA Triton Inference Server의 sequence batching

**Triton이 실제로 해주는 것 (우리가 손으로 짠 것의 표준판):** `[확인됨, NVIDIA 문서]`
- correlation ID로 같은 시퀀스 요청을 같은 인스턴스로 라우팅 = 우리 `per-pid` 라우팅
- `stateful_backend`가 sequence id별 상태 자동 관리 = 우리 hidden state 캐싱
- 시퀀스 시작/끝/ready 신호 = 환자 입원/퇴원 관리

→ "환자별 상태를 들고 이어붙인다"는 우리 설계는 발명이 아니라 **stateful 서빙의 표준 패턴**이다.

**왜 안 썼나:**

| | Triton | 우리 FastAPI |
|---|---|---|
| 적합 규모 | GPU·대규모·다중노드 | **CPU·단일노드** |
| 무게 | 무거움 (TensorRT 변환, control tensor 규약) | 가벼움 (PyTorch 그대로) |
| 번들 운영(R1~R3) | **안 해줌** (서빙만) | 자체 |

Triton은 GPU 고성능 대규모용. CPU 환경에선 핵심 가치(GPU 배칭·가속)가 안 나오는데 설정 복잡도는 그대로 떠안는다. `[확인됨 + 우리 결정]`

**한 줄:** "stateful 시퀀스 서빙은 Triton sequence batcher가 correlation ID로 표준 처리하는 걸 알지만, CPU 단일 노드 규모엔 오버킬이라 FastAPI로 per-pid 상태를 직접 관리했습니다. 확장 시 Triton 하이브리드로 갈 수 있습니다."

---

## 5. 추론 하드웨어 — GPU 대신 CPU

**결정:** 추론은 CPU로 한다. GPU 불필요.

**근거 — 작은 모델 단일추론은 CPU가 오히려 유리:** `[확인됨, 벤치마크]`
- GPU 메모리 전송 오버헤드가 작은 모델 추론 시간을 지배한다. (MobileNet v2: CPU 8ms vs GPU 11ms — CPU가 빠름)
- 작은 신경망(<10M 파라미터)은 CPU에서 훌륭히 동작한다.
- **Microsoft DeepCPU** = 프로덕션에 배포된 CPU 기반 RNN 서빙 라이브러리. → 실무가 실제로 RNN을 CPU로 서빙한다.
- 작은 RNN을 순진하게 GPU에 올리면 커널 런치 지연(~50μs) 때문에 오히려 느려질 수 있다.

**축 구분 (헷갈리지 말 것):**

| 작업 | CPU/GPU |
|---|---|
| 작은 GRU 추론 (단일/저volume) | CPU ✅ |
| 큰 GRU 추론 | GPU (최대 12.5배, GRNN 논문) |
| 대량 배치 추론 (high QPS) | GPU |
| GRU **학습** | GPU 거의 항상 (cf. chest-xray RunPod 예정) |

**한 줄:** "작은 GRU의 환자 단위 실시간 추론은 CPU가 적합합니다. GPU는 전송·커널 오버헤드 때문에 작은 모델 단일 추론에선 오히려 불리하고, Microsoft DeepCPU처럼 프로덕션 RNN도 CPU 서빙이 있습니다. 학습이나 고QPS면 GPU를 검토합니다."

---

## 6. 규모 산정 — 병원당 ≤500명

**결정:** 노드당 모집단을 "병원 입원 환자(≤500명)"로 본다.

**근거:**
- 패혈증 시스템은 클라우드 중앙집중이 아니라 **병원마다 온프레미스 설치**(의료 데이터 반출 민감). `[우리 결정/도메인]`
- 따라서 한 인스턴스가 감당할 모집단 = "그 병원 환자"로 한정. 병원 1000개에 깔려도 각 노드는 독립이라 ≤500명만 본다.
- **"전체는 크지만 노드당은 작다"** — 이게 규모 판단의 핵심.

**한 줄:** "온프레미스 배포라 노드당 모집단이 그 병원 입원 환자(보통 500명 이하)로 한정됩니다. 이 규모는 단일 노드 멀티코어 CPU로 감당됩니다."

> ≤500명이 단일 CPU 노드로 되는지는 `[검증 필요]` — 부하 테스트(500명 동시 입력 시 p99가 SLO 안에 드나)로 확정.

---

## 7. 확장 전략 — CPU scale-out + K8s + Redis (경로)

**핵심 통찰 — 두 축은 별개다:**

| | scale-up (수직) | scale-out (수평) |
|---|---|---|
| CPU 추론 | 코어 증설 | **K8s replicas↑ + Redis** |
| GPU 추론 | 더 센 GPU | K8s + GPU 노드 풀 |

"CPU냐 GPU냐"와 "수직이냐 수평이냐"는 독립 축이다. **CPU로 추론해도 scale-out 하려면 K8s + Redis가 필요하다.** `[우리 결정]`

**왜 K8s + Redis가 한 세트인가:**
- **K8s** — pod(추론 인스턴스)를 늘리고 요청을 분산.
- **Redis** — 그 여러 pod가 같은 환자 상태를 공유하게. stateful 서빙이 scale-out과 양립하게 만드는 접착제.
- K8s 혼자선 stateful scale-out을 못 한다 — Redis가 있어야 상태 공유가 돼서 완성된다.

**현재 vs 확장 (구현은 측정 후):**
- 현재: 병원당 단일 노드, `replicas=1`, hidden state in-memory → ≤500명엔 충분 `[검증 필요]`
- 1차 확장: 수직(코어 증설) — stateful이라 가장 간단
- 2차 확장: 수평(K8s replicas↑) — 단, hidden state를 Redis로 외부화

> **참고:** K8s 배포 자체는 이미 `deploy/k8s`에 있고(`replicas=1`), CKA 학습 맥락에서 어필 포인트. "수평 확장 + Redis"만 측정 후 구현 대상.

**한 줄:** "추론은 CPU로 충분하지만 확장은 별개 축이라, scale-out이 필요해지면 — CPU 추론이어도 — K8s로 pod를 늘리고 stateful 상태를 Redis로 외부화하는 구조를 설계에 넣었습니다. 단일 노드 한계는 부하 테스트로 확인하고 그 시점에 구현합니다."

---

## 8. 상태 관리 패턴 — Redis 외부화 (Triton 라우팅 대신)

**stateful 시퀀스 서빙엔 여러 표준 패턴이 있다:** `[확인됨]`

| 방식 | 상태를 누가 듦 | 라우팅 |
|---|---|---|
| Triton sequence batcher | 인스턴스 / stateful_backend | correlation ID로 같은 인스턴스 |
| **Redis 외부화 + 무상태 노드** | Redis (외부) | 아무 노드나 OK ← 우리 길 |
| sticky session | 노드 메모리 | LB가 같은 노드로 고정 |
| Ray Serve stateful actor | actor 객체 | actor 핸들 |

**Triton vs Redis 트레이드오프:**

| 축 | Triton 승 | Redis 승 |
|---|---|---|
| 상태 접근 속도 | ✅ (메모리) | (네트워크 왕복 ~1ms) |
| GPU 배칭·고성능 | ✅ | |
| 탄력적 scale-out | (인스턴스에 묶임) | ✅ |
| 장애 복원력 | (인스턴스 죽으면 상태도) | ✅ (외부 생존) |
| CPU 환경 적합 | (이득 반감) | ✅ |
| 운영 단순성 | (설정 복잡) | ✅ |

**우리 케이스 대입:** CPU·≤500명·scale-out·의료(복원력 중요)
- Triton이 이기는 축(속도·GPU)이 **우리 케이스에선 둘 다 의미 없다** (SLO에 1ms 무시 가능, GPU 안 씀).
- Redis가 이기는 축(확장·복원력·CPU·단순성)이 **전부 우리 조건에 걸린다.**
- → 우리 맥락에선 Redis가 명확히 낫다.

**한 줄:** "stateful 서빙엔 상태를 인스턴스에 두고 라우팅하는 방식(Triton)과 Redis로 외부화하는 방식이 있습니다. 전자는 GPU 고성능에, 후자는 CPU scale-out에 강합니다. 제 케이스는 CPU 분산 확장이라 Redis 외부화를 택했습니다."

> **정직한 단서:** GPU 대규모 저지연(실시간 음성·고QPS)이었으면 Triton이 나았을 것. 그건 우리 워크로드가 아니다.

---

## 9. 장애 대비 — Redis HA (경로)

**문제:** 단일 Redis는 그 자체로 SPOF. 죽으면 모든 환자 상태가 한 번에 날아간다. `[확인됨]`

**단계적 대비:** `[확인됨, Redis 표준 기능]`
1. **영속화 (AOF/RDB)** — 디스크 백업. 죽었다 살아나면 복구.
2. **복제 (Replica)** — Primary 옆에 실시간 복사본.
3. **Sentinel** — Primary 죽음 감지 → Replica 자동 승격 (무중단).

**현재 vs 확장:**
- 1단계(현재): in-memory, `replicas=1` → Redis 불필요
- 2단계(scale-out 시): Redis 단일 + AOF 영속화 → 죽어도 디스크 복구
- 3단계(무중단 필요 시): Redis Sentinel → 자동 failover

**한 줄:** "단일 Redis는 SPOF라 영속화→복제→Sentinel failover로 단계적으로 대비합니다. 현재 규모엔 단일 Redis + AOF로 충분하지만, 실제 의료 프로덕션이면 무중단 요건상 Sentinel 기반 HA가 필요합니다."

> **의료 도메인 단서:** 패혈증 모니터링은 상태 끊김이 위험하므로, 실제 프로덕션이면 HA가 "오버"가 아니라 "필수"로 격상된다. `[우리 결정/도메인]`

---

## 10. 확장 로드맵 (전체 묶음)

```
[현재] 병원당 단일 노드 · replicas=1 · hidden state in-memory · CPU
   │     ≤500명엔 충분 (부하 테스트로 검증 예정)
   │
   ├─ 트리거: 단일 노드가 그 병원 부하를 못 견딤 (p99 > SLO)
   │
   ▼
[1차] 수직 확장 (코어 증설)
   │     stateful이라 가장 간단. 상태 그대로 in-memory.
   │
   ├─ 트리거: 수직으로도 부족 / 가용성 요구 상승
   │
   ▼
[2차] 수평 확장 (K8s replicas↑ + Redis 외부화 + AOF)
   │     hidden state를 Redis로 빼야 scale-out 성립.
   │
   ├─ 트리거: 무중단(의료 안전) 요구 명확화
   │
   ▼
[3차] Redis HA (Sentinel) + (필요시) Triton 하이브리드
         상태 저장소 자동 failover. 고성능 필요시 추론을 Triton으로 분리.
```

**전체를 관통하는 원칙:** 추정으로 미리 깔지 않는다(measure-then-scale). 각 단계는 *트리거*가 확인됐을 때 구현한다. 지금 할 일은 "구현"이 아니라 "경로를 명시"하는 것.

---

## 부록 — 이 문서가 대비하는 면접 질문

- "왜 MLflow Registry 안 썼어요?" → §2 (번들 단위)
- "왜 pyfunc 안 썼어요?" → §3 (hidden state 못 가둠)
- "왜 Triton 안 썼어요?" → §4 (CPU 단일노드 오버킬)
- "GRU를 CPU로 추론해요?" → §5 (작은 모델은 CPU 유리)
- "확장은 어떻게 해요?" → §7 (두 축 분리, K8s+Redis 경로)
- "Triton이 더 낫지 않나요?" → §8 (축마다 갈림, 우리 맥락은 Redis)
- "Redis가 죽으면요?" → §9 (AOF→복제→Sentinel)

**모든 답의 공통 구조:** "그 도구가 뭘 하는지 안다 → 우리 워크로드 규모를 안다 → 그래서 이 시점엔 X, 트리거가 오면 Y." 도구 우열이 아니라 *시점 판단*을 보여준다.