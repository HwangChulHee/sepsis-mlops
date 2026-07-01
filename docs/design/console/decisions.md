# Console 설계결정문서 (DDD) — 운영 콘솔 (R1~R3)

> **설계 근거**: H4 운영 레이어(서빙·드리프트·재학습)가 만든 백엔드(`deploy.py`·`validate.py`·`bundle.py`)를 **사람이 조작하는 운영 콘솔**로 노출. 모델을 *만드는* 건 코드+MLflow(콘솔 밖), 콘솔은 *운영*(배포 승인·버전·감사)만.
> **워크플로우·출처등급**: [`WORKFLOW.md`](../WORKFLOW.md). 검토(`docs/design/console/review.md`) 통과 후 구현 명세부로.
> **상태**: 설계부 v4 — 레드팀 라운드 4 신규 blocker B-new(트랜잭션 경계·직렬화·복구) + major 2건 반영. 구현 명세부는 blocker 0 확정 후 이어붙임.
> **개정 이력**
> - v1: 초안 (설계부).
> - v2: 레드팀 라운드 1 반영 — B1(alias 전파 메커니즘 결정·서빙 1점 범위 편입), B2(MLflow 연결 키 = meta.json.run_id, 교차단계 의존 명시 + 정직한 폴백), M1(SQLAlchemy 허위 `[확인됨]` 강등), M2(롤백 승인·감사 정책), M3(REGRESSED 비승격 = 하드 게이트 명시), M4(actor 미검증 입력으로 정직화·규제 주장 완화).
> - v3: 레드팀 라운드 2 반영 — **N1**(게이트결과·재학습상세를 version dir에 영속 = `validation.json`+`retrain.json`, swap 복원 경로 명시 → 결정 5-B 신설, 결정 1 challenger 디스크 표식·결정 4/6-A 스냅샷 실데이터화), MJ1(reload alias 1회 해석·동시 로드·로드 중 swap 배제), MJ2(run_id 부재 시 `/health` 식별 오염 — N1/B2로 해소), mn1(archived 콜드스타트 시드 정책), mn2(6-A 폴백 문구 한정).
> - v4: 레드팀 라운드 4(흐름추적+검토깊이) 반영 — **B-new**(승인/롤백 = FS+DB 다중저장소 트랜잭션, 직렬화 경계·권위·재기동 화해 결정 → **결정 7 신설**, 결정 1 archived=과거활성 한정·현재 champion=alias 권위, 결정 2 직렬화 전제 한정, challenger 판별 두 파일 AND), MJ-new1(reload 트리거 실패·전파 확인 — 결정 2-A 전파 확인 절), MJ-new2(백엔드 ValueError = 단일출처 두번읽기, `validation.json`=신뢰경계 정직화 — 결정 3·5-A), mn-a(challenger=두 파일 AND·원자 기록), mn-b(eps·시각 영속 주입), mn-c(prev 감사 캡처), mn-new1(롤링 재시작 transient 혼재 수용), mn-new2(validation.json 신뢰경계 MVP 가정).

## 한 줄 요약

H4가 만든 버전 관리·검증 게이트·롤백 백엔드를, **champion-challenger 운영 콘솔** 하나로 사람이 조작하게 한다. R1(배포 승인)·R2(버전·롤백)·R3(감사)를 "버전 리스트 → 클릭 → 상세+액션+감사" 한 화면에 통합. **추론(stateful)과 콘솔(stateless)은 독립 앱으로 분리**(scale-out 로드맵). 실험·수치 상세는 재구현하지 않고 MLflow로 링크. **운영 본체 — 모델 성능이 아니라 운영 설계가 차별점**(Epic 실패 vs TREWS 교훈).

## 범위 / 범위 외

| 범위 (console) | 범위 외 |
|---|---|
| 배포 승인 UI(게이트 표시 + 승인/기각) | 모델 학습·재학습 *로직* (코드로, 콘솔 밖) |
| 버전 리스트·활성 표시·롤백 UI | 실험 추적·수치 비교 (MLflow Tracking) |
| 감사 추적(승인·교체·롤백 기록) | 점진적 배포(canary/blue-green/shadow) |
| 콘솔 백엔드 API(기존 함수 노출) | 서빙 프레임워크 도입(Triton/Seldon/KServe — ADR 참고) |
| Grafana 운영 패널 2개(SLO·헬스) | 멀티 병원·멀티 모델 |
| **서빙 활성 버전 전파**(alias 기반 로딩 + 재로드 트리거) — B1로 편입, 예측 *로직*은 불변 | 인증/SSO·신원 검증 (M4 후속, 미검증 actor로 시작) |

> **v2 범위 변경(B1)**: 원래 "서빙 수정 = 범위 외"였으나, 레드팀이 alias 스왑이 실행 중 서빙에 전파되지 않음을 지적(B1). 콘솔의 핵심 가치(운영 일관성)를 코드로 지탱하려면 서빙의 **버전 소스를 alias로 바꾸고 재로드 경로를 추가**하는 최소 변경이 불가피하다. 이 1점만 범위에 편입하되, 예측/추론 로직·환자별 상태 처리는 콘솔이 절대 건드리지 않는다(환자 안전 분리 원칙 유지).

---

## 결정 1: 통합 콘솔 — champion-challenger, 리스트→클릭→상세

- **결정**: R1·R2·R3를 화면 3개로 나누지 않고 **하나의 콘솔**로 통합한다. 구조 = 상단 활성(champion) 상태바 → 버전 리스트(champion/challenger/archived) → 행 클릭 시 상세 펼침(번들·게이트·액션·감사·MLflow 링크). champion-challenger는 업계 표준 프레이밍.
- **근거 + 출처등급**:
  - 버전 리스트→클릭→상세 pane은 Seldon/BentoML 대시보드 표준 패턴 [확인됨: 벤치마크].
  - R1·R2·R3가 모두 "버전을 대상으로 한 운영 행위"라 한 패턴으로 흡수됨 [우리 결정].
- **고려한 대안**: 화면 3개 분리(중복 네비게이션, 같은 버전을 세 곳에서). 기각.
- **champion/challenger/archived 도출 기준** (m3 — 백엔드엔 "challenger" 개념이 없음 [확인됨: deploy.py는 active alias + 버전 디렉토리만 안다]). 콘솔이 다음 규칙으로 *파생*한다 [우리 결정]:
  - **champion** = 현재 alias가 가리키는 버전 (`deploy.active_version(fs)`, `deploy.py:46`). **현재 활성 상태의 권위는 항상 alias이다**(감사 DB가 아님 — 결정 7-2의 권위 결정과 일치).
  - **challenger** = 자재화(materialize)되어 `gru_<fs>@<v>` 디렉토리로 존재하고, **`validation.json` *과* `retrain.json`이 그 디렉토리에 함께 영속되어 있으며**(결정 5-B / N1, mn-a) 아직 활성이 아닌 버전. → "검증·재학습 출처가 모두 있다"의 디스크 표식 = **두 파일 모두 존재**. **둘 중 하나라도 없는 버전 디렉토리는 *미완성 후보*로 분류**(자재화는 됐으나 게이트/재학습 출처 미기록·부분쓰기 → 승인 대상에서 제외, "검증 대기"로 표시). 두 파일을 묶은 이유 = 부분쓰기(`validation.json`만 기록 후 `retrain.json` 실패) 시 "승인 가능하나 재학습 출처 공백"이 재발하므로(mn-a), 판정을 두 파일 AND로 걸고 원자 기록을 5-B 의존으로 둔다. 이로써 challenger 판별이 in-memory가 아니라 파일시스템에서 결정된다.
  - **archived** = 과거에 champion이었다가 교체/롤백으로 비활성이 된 버전 — **출처 = 감사 저장소의 swap/rollback 이력**(파일시스템엔 "과거 활성" 표시가 없으므로 감사 DB가 *과거 활성 도출*의 source of truth). **단, "현재 누가 champion인가"는 감사가 아니라 alias로 읽는다**(결정 7-2). 즉 감사 DB는 *이력(history)*의 권위지 *현재 활성 상태*의 권위가 아니다. ②후 ③전 크래시로 alias와 감사가 분기하면 alias가 이기며, 재기동 시 화해(결정 7-2)로 감사를 실제 상태에 맞춘다.
- **archived 콜드스타트 (mn1)** [우리 결정]: 감사 DB가 비어 있는 1일차에는 과거 champion 이력이 없어 현재 champion 외 버전이 archived로 보이지 않는다. **시드 정책**: 콘솔 부트스트랩 시 현재 alias가 가리키는 champion을 1건 *seed audit 레코드*(action=`bootstrap`, actor=`system`)로 기록해 "현재 활성"의 출처를 감사에도 남긴다. 콘솔 도입 *이전*의 과거 swap 이력은 감사 DB에 소급 복원할 수 없으므로(파일시스템에 표식 없음), 1일차 archived 목록은 비어 있는 것이 정상임을 UI에 명시한다(거짓 복원 금지). 이후 모든 교체/롤백이 감사에 누적되며 archived가 채워진다.
- **검토 요청 항목**: 한 화면 통합이 R1·R2·R3 기능을 빠짐없이 담는지.

---

## 결정 2: 서빙 앱 / 콘솔 앱 분리 (독립 앱 2개)

- **결정**: 추론 서빙(`src/sepsis/serve/app.py`)과 운영 콘솔을 **독립 앱 2개**로 둔다. 추론은 stateful(환자별 hidden state)·scale-out 대상·환자 안전 직결, 콘솔은 stateless·1개면 충분·사람이 자주 조작. **공유 자원 = 번들 저장소(active alias) + 감사/상태 DB.**
- **"1개면 충분"의 정확한 범위 (B-new 면 2)** [우리 결정]: 이는 *배포 인스턴스 수*(scale-out 불필요) 얘기지 *요청 동시성*이 해소된다는 뜻이 아니다. FastAPI는 한 프로세스에서 요청을 동시 처리하므로 운영자 2명이 서로 다른 challenger를 동시에 승인할 수 있다(B-new 면 2). 따라서 승인/롤백의 동시성은 **결정 7의 직렬화 경계로 별도 차단**한다. 콘솔이 1 프로세스인 한 프로세스-로컬 직렬화로 닫히지만, 향후 다중 프로세스/replica로 가면 공유 저장소 기반 락으로 승격해야 함을 의존으로 식별한다(결정 7-1).
- **근거 + 출처등급**:
  - 추론이 stateful이라 scale-out 시 replica 복제 대상인데, 콘솔이 같이 복제되면 승인/롤백이 pod마다 흩어져 활성 버전이 불일치 [우리 결정, K8s scale-out 로드맵].
  - 콘솔 변경이 추론(환자 안전)을 건드릴 위험 제거 [우리 결정].
- **고려한 대안**: 한 앱 통합 + `/console` router 격리(단순하나, scale-out 시 분리 강제). scale-out 로드맵이 실재하므로 처음부터 분리.

### 2-A: alias 변경의 서빙 전파 메커니즘 (B1 — 핵심 일관성 주장 지탱)

- **레드팀이 드러낸 단절** [확인됨]: 서빙은 alias를 읽지도 핫리로드하지도 않는다. (1) 번들을 모듈 전역 `_S`에 **한 번 로드 후 영구 캐시**, 재로드 트리거 없음(`app.py:34-44`). (2) 컨테이너 경로는 alias가 아니라 ConfigMap이 주입한 **고정 `SERVE_BUNDLE_DIR`($RUN 디렉토리)** 를 읽음(`app.py:38-40`). drift baseline도 `_DS`에 영구 캐시(`app.py:97-110`). → 콘솔이 `deploy.swap`/`rollback`으로 심볼릭링크를 바꿔도 실행 중 pod는 옛 번들을 계속 쓴다. "alias = 단일 조정점"이 실제 로딩 경로와 단절.
- **결정 (근본 해소)** [우리 결정]: 활성 버전의 **단일 진실원천(source of truth) = alias `gru_<fs>`** 로 통일하고, 두 가지를 결정한다.
  1. **서빙 버전 소스를 alias로 변경** — 컨테이너 서빙이 읽는 경로를 고정 `$RUN`이 아니라 alias `gru_<fs>`(→ 현재 버전 디렉토리로 심볼릭링크)로 바꾼다. ConfigMap은 더 이상 특정 `$RUN`을 핀하지 않고 alias 경로만 가리킨다. 이로써 dev(MLflow)·컨테이너(export dir) 양쪽이 *같은 활성 표식*을 본다. (서빙 1점 변경 — 범위표 v2 참고.)
  2. **전파(실행 중 pod 반영) = 명시적 단계** — alias 교체는 *디스크의 활성 표식*만 바꿀 뿐 메모리 캐시 `_S`/`_DS`는 갱신하지 않는다. 따라서 콘솔 swap/rollback은 alias 갱신 **후** 서빙 재로드를 *명시적으로 트리거*한다:
     - **프로덕션(K8s) = 롤링 재시작** [우리 결정]. 콘솔이 서빙 Deployment에 rolling restart를 트리거(예: `kubectl.kubernetes.io/restartedAt` 어노테이션 패치)한다. 새 pod가 갱신된 alias를 fresh 로드, 옛 pod는 graceful drain. 모델 *버전* 교체이므로 옛 GRU의 hidden state를 새 모델로 이어쓰면 안 되며, 재시작에 따른 **상태 리셋은 버전 교체 시 올바른 동작**이다(혼용이 오히려 skew). 콘솔에 필요한 권한 = 해당 Deployment 재시작용 **좁은 RBAC**(patch deployment만).
       - **drain 중 transient 버전 혼재 수용 (mn-new1)** [우리 결정]: graceful drain 동안 새 pod와 옛 pod가 두 모델 버전을 잠시 동시 서빙한다. 환자별 추론은 **요청 단위 stateless**(hidden state는 요청 내 시퀀스로 재구성, pod 간 공유 상태 없음)이고 두 버전 모두 *검증·승인된 번들*이므로, 이 transient 혼재는 의료적으로 허용 범위로 수용한다. 무중단·단일버전 보장이 핵심 요구면 in-place `/admin/reload` 경로를 택일한다(아래 정의됨).
     - **dev/로컬(K8s 없음) = 재로드 엔드포인트** [우리 결정]. 서빙에 `POST /admin/reload`(루프백/관리 토큰 가드)를 추가, `_S`/`_DS`를 alias 기준으로 재초기화. 콘솔이 swap/rollback 후 호출.
  3. 콘솔은 **예측/추론 로직과 환자별 상태 처리를 건드리지 않는다** — 추가되는 것은 (a) alias 기반 번들 소스, (b) 재로드 진입점뿐. 환자 안전 분리 원칙 유지.
- **전파 확인·실패 처리 — 사슬의 마지막 홉이 조용히 끊기지 않게 (MJ-new1)** [우리 결정 / 검증 필요]: alias·감사 갱신 후 reload 트리거(롤링 재시작 | `/admin/reload`)는 실패할 수 있다(K8s API 거부·`/admin/reload` 500·네트워크 단절). 이 경우 alias·감사는 새 버전인데 서빙은 옛 `_S`/`_DS`를 계속 써 성공기준 "옛 번들 잔존 없음"이 *조용히* 위반된다. 따라서 전파를 결과를 모르는 fire-and-forget로 두지 않고 **확인 의존**을 못 박는다: (a) **전파 성공 확인** = swap/rollback 후 서빙 `/health`의 `run_id`가 **현재 `active_version`(alias가 가리키는 버전)** 과 일치하는지 폴링(MJ2/결정 6-A 보강으로 `/health`가 실제 `run_id`를 보고함이 전제). **타겟은 *그 swap이 쓴 버전*이 아니라 *현재 alias***이다(MJ-r5): 결정 7-1의 직렬화로 연속 승인 A(→V2)·B(→V3)가 순차 통과하면 alias·서빙은 V3로 수렴하는데, 확인 타겟을 "그 swap의 버전(V2)"으로 두면 A의 폴링이 이미 정당히 대체된 V2를 영원히 "전파 대기/실패"로 거짓 표시한다. 타겟을 현재 alias로 정의하면 A·B 모두 V3 수렴 시 "확인됨"으로 닫히고, 진짜 미전파(서빙이 alias를 못 따라옴)는 여전히 `active_version` 불일치로 표면화된다(alias=현재 활성 권위 — 결정 7과 정합), (b) **실패 시 재시도·운영자 경보**, (c) **확인 전까지 콘솔 UI는 그 버전을 "활성(전파 확인됨)"이 아니라 "전파 대기/실패"로 구분 표시**. 핵심: alias·감사는 권위(결정 7)라 이미 갱신됐고, *미전파는 숨기지 않고 가시적 상태로 노출*한다. 폴링 주기·재시도 백오프·경보 채널 등 기법은 명세부, 서빙 `/health` 의존이라 [검증 필요].
- **reload 원자성 — `_S`/`_DS` 분리 lazy-load 스큐 방지 (MJ1)** [확인됨: app.py 분리 캐시 / 검증 필요]: 고정 `$RUN`을 가변 alias로 바꾸면 `_S`(첫 `/predict` 시 로드)와 `_DS`(첫 `/drift` 시 로드)가 *다른 시점*에 alias를 해석해 모델↔drift reference가 서로 다른 버전을 가리키는 스큐(거짓 드리프트)가 생길 수 있고, `load_bundle_from_dir`는 model+stats+reference 비원자 로드라 로드 도중 swap이 일어나면 혼합 번들이 만들어질 수 있다. → 구현 명세부에서 reload 규약을 못박는다: (i) **reload는 alias 타겟을 1회만 해석**해 그 *고정된 버전 dir*에서 `_S`·`_DS`를 **함께(동일 버전)** 로드, (ii) **로드 중 swap 배제**(reload와 alias 교체를 락/순서로 직렬화 — alias 갱신 → 그 다음 reload), (iii) 첫 요청 lazy-load도 같은 해석 시점을 쓰도록 부팅 시 eager 로드 또는 공유 해석 캐시를 둔다. 서빙 코드 변경이라 [검증 필요].
- **미해결로 정직히 남기는 부분** [검증 필요]:
  - 프로덕션 전파를 *롤링 재시작*으로 할지 *in-place `/admin/reload`*로 할지의 최종 택일은 배포 환경(K8s 유무·무중단 요구·drain 정책)에 종속 — 운영 배포 시점에 확정. 본 DDD는 두 경로를 모두 정의하되 K8s 기본값을 롤링 재시작으로 제안. (프로덕션 롤링 재시작 경로는 새 pod가 부팅 시 alias를 1회 해석해 fresh 로드하므로 MJ1 스큐가 구조적으로 없음 — in-place reload 경로에서만 위 (i)~(iii) 규약이 필요.)
  - 콘솔이 K8s API에 접근하기 위한 정확한 RBAC role/binding과 네트워크 정책은 인프라 결정 — 구현 명세부 또는 별도 ADR에서 확정.
- **검토 요청 항목**: alias 단일 진실원천 + (롤링 재시작 | reload 엔드포인트) 전파가 "콘솔 swap → 실행 중 서빙 반영"을 실제로 닫는지, 그리고 서빙 1점 변경이 환자 안전 분리를 깨지 않는지.

---

## 결정 3: 검증 게이트 표시 — 수치 크게 + 판정 보조

- **결정**: 배포 승인 화면에서 `validate.py`가 생성하는 게이트 결과를 **수치를 주인공으로 크게, 판정(PASS/REGRESSED)을 보조 신호로** 표시. 사람이 수치를 보고 판단하되 판정이 주의를 환기.
- **게이트 실체** (`validate.py` `ValidationResult`):
  - **B-holdout 성능** — 새 운영 데이터(B holdout)에서 util/PR-AUC [확인됨: validate.py].
  - **A-val 무회귀** — `새 util ≥ 옛 util − eps(0.02)`. = catastrophic forgetting 체크. 각자 자기 stats로 채점 [확인됨: validate.py].
  - **cross_site_claim = false** — 통과/실패가 아닌 **정직성 플래그**. "A+B 학습이라 in-distribution이지 cross-site 일반화 아님" [확인됨: validate.py].
- **두 게이트의 성격 구분 (M3·m2 — 혼동 금지)** [확인됨: 코드]:
  - **하드 게이트 = A-val 무회귀 하나뿐.** `no_regression`이 PASS/REGRESSED를 가르는 유일한 판정이며(`validate.py:59`), `deploy.swap`은 `no_regression=False`면 **사람이 승인해도** `ValueError`로 거부한다(`deploy.py:61-62`). 즉 **REGRESSED 버전은 비승격(non-promotable)** 이다 — catastrophic forgetting 방지를 *자문이 아니라 강제*로 둔 의도된 설계.
  - **B-holdout 성능 = informational 신호**(통과 임계값 없음). `validate.py`에 B-holdout 임계 판정이 없으므로(m2) 콘솔은 B-holdout을 "게이트"가 아니라 *참고 수치*로 표시한다. PASS/REGRESSED를 B-holdout에 결부시키지 않는다.
- **콘솔 UI 귀결 (M3)** [우리 결정]: 승인 화면은 두 단을 분리한다 — (1) **검증 게이트(자동·하드)**: REGRESSED면 승인 버튼 자체를 비활성화하고 "A-val 회귀로 백엔드가 swap을 거부함"을 표시(승인해도 실패하므로). (2) **사람 승인**: PASS 후보에 대해서만 활성. 백엔드에 REGRESSED 우회 경로는 없으며, 만약 향후 오버라이드가 필요하면 `deploy.swap` 코드 변경이 따로 필요(현 범위 밖, [검증 필요]).
- **방어선 성격 정직화 — "이중 게이트"의 한계 (MJ-new2)** [확인됨: 코드]: UI 버튼 비활성과 백엔드 `ValueError`(`deploy.py:61`)를 "이중 게이트"라 부르되, **이는 진정한 다출처(독립) 방어가 아니다.** N1(결정 5-B) 이후 콘솔은 `validation.json`을 읽어 객체로 복원해 `swap`에 건네고, `swap`은 그 *콘솔-복원 객체*의 `no_regression`만 본다 — **UI도 백엔드도 결국 같은 `validation.json` 파생**이다. 따라서 백엔드 가드가 막는 것은 *콘솔 로직 누락(가드 드롭, 예: UI 버튼 비활성 코드 버그로 REGRESSED를 승인 시도)*이지, **`validation.json` 자체가 stale/오염이면 백엔드도 못 잡는다**(같은 틀린 값을 두 번 읽을 뿐). 방어 깊이의 정확한 정의 = **가드 드롭 방지(코드 경로 이중화) ≠ 데이터 무결성 방어**.
- **`validation.json` = 신뢰경계 (mn-new2)** [우리 결정]: 위 귀결로 `validation.json`은 **하드 게이트의 신뢰경계**다. 이를 직접 편집하면 하드 게이트(A-val 무회귀)를 우회해 REGRESSED를 승인 가능하게 만들 수 있다. MVP는 이 파일이 H4r 파이프라인만 쓴다는 전제 아래 **신뢰경계를 신뢰하는 가정으로 수용**한다(파일 무결성 서명·쓰기 권한 격리·인증된 actor는 후속 과제 [검증 필요]). 이 가정을 숨기지 않고 명시하며, 거짓 "다중 방어선" 주장으로 포장하지 않는다.
- **근거 + 출처등급**: 최종 판단은 사람 몫(human-in-the-loop)이라 수치 투명성이 우선, 판정은 안전망 [우리 결정].
- **검토 요청 항목**: 표시할 게이트 항목이 `ValidationResult`의 실제 필드와 일치하는지(코드가 source of truth).

---

## 결정 4: 감사 저장소 — SQLAlchemy ORM, DB 교체 가능

- **결정**: 승인·교체·롤백 이벤트를 영구 기록하는 감사 저장소를 **SQLAlchemy ORM**으로 신규 구현. SQLite로 시작, **PostgreSQL로 교체 가능**하게 추상화. 기록 항목: 시각·행위(approve/swap/rollback, 그리고 시스템 행위 `bootstrap`/`reconcile` — 결정 1·7)·대상 버전·**이전 활성 버전(prev, mn-c)**·featureset·행위자·근거·게이트 결과 스냅샷.
- **게이트 스냅샷 출처 (N1 — in-memory 아님)** [우리 결정]: 승인 시점 캡처하는 "게이트 결과 스냅샷"의 데이터 원천은 **version dir의 `validation.json`/`retrain.json`**(결정 5-B로 영속)이다. 즉 콘솔은 휘발성 `ValidationResult`를 들고 있을 필요 없이, 승인 대상 버전 dir에서 영속 파일을 읽어 그 사본을 감사 레코드에 박는다. 영속 파일이 없는 버전(=미완성 후보)은 애초에 승인 대상이 아니므로 빈 스냅샷이 기록될 일이 없다.
- **근거 + 출처등급 (M1 — 허위 `[확인됨]` 강등)**:
  - ~~레포가 이미 SQLAlchemy 사용 [확인됨: 메모리·코드].~~ **정정**: `src/` 전체에 `sqlalchemy`/`create_engine`/`declarative_base`/`sessionmaker` 사용처 **0건**(grep 확인). MLflow가 의존성으로 SQLAlchemy를 *전이적으로* 끌어올 뿐, 우리 코드는 직접 쓰지 않는다. → **SQLAlchemy ORM 채택은 신규 의존 도입 [우리 결정]**이다.
  - 다만 **sqlite를 저장 백엔드로 쓰는 패턴은 레포에 실재** — `serve/bundle.py:115`가 MLflow tracking을 `sqlite:///{ROOT}/mlflow.db`로 쓴다 [확인됨: bundle.py:115]. "sqlite 파일 DB"는 기존 패턴이나, "SQLAlchemy ORM 계층"은 우리가 새로 더하는 것임을 구분한다.
  - 의료 규제 정합성: 감사 추적의 *구조*(불변 append 로그·행위/대상/시각·게이트 스냅샷)는 규제 기조와 정렬되나, **규제 수준의 행위자 귀속은 인증이 전제**다(M4 참조). 따라서 "의료 규제 직결"은 *구조 정렬*로 한정하고, 검증된 귀속은 후속 과제로 둔다 [우리 결정].
- **행위자(actor) 출처 (M4 — 미검증 입력으로 정직화)** [우리 결정]:
  - 현 MVP에는 **인증/SSO·신원 검증이 범위에 없다**(범위표 v2). 따라서 `actor` 필드는 MVP에서 **운영자가 제출하는 미검증(unverified) 입력**이다. 이를 감사 스키마에 `actor_unverified`로 명시 표기하고, 콘솔/문서 어디에서도 "검증된 신원"으로 주장하지 않는다.
  - 향후 SSO/OIDC 연동 시를 위해 스키마에 `verified_subject`(검증된 주체 식별자) 컬럼을 예약하되 MVP에선 null. 인증 도입은 명시적 후속 과제 [검증 필요].
- **고려한 대안**: 평면 로그 파일(쿼리·교체 어려움). 기각.
- **검토 요청 항목**: 감사 기록 시점(승인/롤백 엔드포인트 내부에서 자동 기록되는지) — 결정 5에서 API 경계 강제로 확정.

---

## 결정 5: 백엔드 재활용 — 기존 함수를 `/console` API로 노출

- **결정**: 버전·검증·배포 로직을 재구현하지 않고 **기존 함수를 API로 노출**만 한다.
  - `deploy.active_version(featureset)` → 활성 버전 조회 [확인됨: deploy.py:46]
  - `validate.*` → 게이트 결과 [확인됨: validate.py]
  - `deploy.swap(featureset, version_dir, *, validation, approved)` → 승인 시 교체. **`approved is not True`면 `PermissionError`**, **`validation.no_regression=False`면 `ValueError`**(둘 다 raise) [확인됨: deploy.py:55,59-62]. `validation`·`approved`는 **keyword-only**(`*` 뒤)이므로 API는 키워드로 호출해야 한다(m1) [확인됨: deploy.py:55].
  - `deploy.rollback(featureset, previous_version_name)` → 롤백 [확인됨: deploy.py:68]
  - `bundle.set_alias` → 원자 스왑(`os.replace`) [확인됨: bundle.py:38].
- **버전 단위 주의**: `active_version`이 **featureset 단위**다 [확인됨: deploy.py:46]. 콘솔의 버전 리스트도 featureset 단위로 표현해야 백엔드와 정합.

### 5-A: 롤백의 승인·감사 정책 (M2 — 백엔드가 우회·무감사)

- **레드팀 지적** [확인됨]: `deploy.rollback`은 approval/validation 체크 없이 곧장 `set_alias`만 호출하고 감사 훅이 없다(`deploy.py:68-70`). 롤백도 활성 버전을 바꾸는 "교체"인데 승인·감사가 비어 있다.
- **결정 (정책 확정)** [우리 결정]:
  - **롤백 = 승인 게이트 적용 + 감사 필수.** 콘솔에서 롤백은 swap과 동일하게 사람 승인을 요구하고, 반드시 감사에 기록한다(성공 기준 "모든 롤백이 감사에 기록됨"을 실제로 강제).
  - **롤백에는 validation 게이트를 적용하지 않는다 (의도)**: 롤백 대상은 *과거에 이미 champion으로 검증·승인되었던* 버전이다. 인시던트 복구 중 재검증을 강제하면 회복을 막는 역효과. 따라서 "승인 + 감사"는 요구하되 `no_regression` 재검증은 요구하지 않는다.
  - **강제 지점 = 콘솔 API 경계** [우리 결정]: 백엔드 `deploy.rollback`엔 승인/감사 훅이 없으므로, 콘솔 API가 `deploy.rollback` 호출 *전에* (1) 승인 여부 확인 → 없으면 거부, (2) 감사 레코드 기록을 강제한다. swap도 동일하게 API 경계에서 감사를 기록한다. `deploy.swap`의 `PermissionError`(미승인)·`ValueError`(REGRESSED)는 **코드 경로 가드 드롭을 막는 방어선이지 감사원도 아니고 데이터 무결성 방어도 아니다** — `ValueError`는 `validation.json` 파생값(`no_regression`)을 보므로 그 파일이 오염이면 못 막는다(MJ-new2 참조).
  - **방어 심화(교차단계 권고, [검증 필요])**: 직접 `deploy.rollback` 호출이 API를 우회할 수 있으므로, H4r에 `deploy.rollback`도 `swap`처럼 `*, approved: bool` 가드를 추가하고 prev를 반환하도록 대칭화할 것을 권고. 이는 콘솔 단계가 아닌 H4r 코드 변경이라 본 DDD에선 권고로만 남긴다.
    - **[H4r 일부 구현됨, 커밋 `790a285`]**: 콘솔 경계(`service.rollback`)에 롤백 대상이 `archived`(과거 활성 이력)인지 강제하는 게이트를 추가(`_classify` 재사용 → `ValueError` → API 422)해 **BR2-1**(프론트 핸드오프 검토에서 발견 — REGRESSED/미활성 challenger를 롤백으로 승격하는 우회)을 **콘솔 API 경로에서 닫았다**. 이는 결정 5-A의 "강제 지점 = 콘솔 API 경계" 원칙과 정합.
    - **[H4r 대칭화도 구현됨]**: `deploy.rollback`에 `swap`과 대칭인 `*, approved: bool` 가드(미승인 시 `PermissionError`) + prev 반환을 추가(`deploy.py`, 테스트 `tests/console_prep/test_deploy_rollback_symmetry.py`). 이제 콘솔 API를 우회한 직접 import 호출도 무승인이면 차단된다. `service.rollback`은 승인 경계로 `approved=True`를 넘긴다. 단 이 가드는 "승인 여부"만 보므로, **archived 강제(REGRESSED 비승격)의 권위는 콘솔 경계 `_classify` 게이트로 유지**된다(deploy 가드는 그 위의 백스톱).
- **근거 + 출처등급**: H4가 검증·승인·롤백 *기본 동작*은 구현, 콘솔은 노출 + **승인/감사 강제 경계** 계층 [확인됨: 코드 / 우리 결정].
- **검토 요청 항목**: 노출하려는 엔드포인트가 실제 함수 시그니처(keyword-only 포함)와 맞는지. `swap`의 `approved`·`validation` 인자가 API에서 어떻게 채워지는지. 롤백 승인/감사가 API 경계에서 빠짐없이 강제되는지.

### 5-B: 게이트 결과·재학습 상세의 version dir 영속 + swap 복원 경로 (N1 — in-memory 의존 제거)

- **레드팀이 드러낸 단절** [확인됨: 코드]: version dir에 영속되는 것은 `model.pt`·`pre.npz`·`meta.json`(4필드: `featureset/hp/input_dim/tau/version/trained_on`)·`reference.npz`뿐이다(`deploy.py:33-42`). `validate()`는 in-memory `ValidationResult` dataclass를 반환할 뿐 디스크에 쓰지 않고(`validate.py:60`), `RetrainResult`의 재학습 상세(`epochs`·`val_loss`·`b_retrain`/`b_holdout`/`train_pids`)도 어디에도 저장되지 않으며(`pipeline.py:31-47`) `seed`·`run_id`·`git_commit`은 결과 객체에조차 없다. → **콘솔이 시간 분리된 materialized dir만 보는 시점엔 `ValidationResult`가 이미 사라졌다.** 그런데 `deploy.swap(...)`은 `validation` 인자를 요구하고 `getattr(validation,"no_regression",False)`를 본다(`deploy.py:55,61`). 즉 콘솔 핵심 액션(승인→swap)이 디스크만으로는 **재구성 불가**이고, 결정 1 challenger 도출·결정 4 게이트 스냅샷·결정 6-A 폴백이 모두 "그 시점에 데이터가 손에 있다"는 성립 불가 가정 위에 있었다.

- **결정 (근본 해소 — 디스크를 단일 진실원천으로)**:
  1. **`validation.json`을 version dir에 영속 (교차단계 의존 H4r, [검증 필요])** — `validate()` 직후(또는 `materialize()`가 `validation`을 받아) `gru_<fs>@<v>/validation.json`에 `ValidationResult` 전체를 직렬화한다: `no_regression`(하드 게이트), `bholdout_util`/`bholdout_prauc`(informational), `new_aval_util`/`old_aval_util`/`new_aval_prauc`/`old_aval_prauc`, `eps`, `cross_site_claim`, `distribution`, `note`, 그리고 검증 시각. 이 파일 존재 = 결정 1의 challenger 디스크 표식.
     - **`eps`·검증 시각 주입 명시 (mn-b)** [확인됨: 코드]: `eps`는 `ValidationResult`의 dataclass 필드가 아니라 `validate(*, eps=0.02)`의 *인자*이고(`validate.py:46`), 검증 시각 필드도 없다(`validate.py:31-43`). 따라서 "전체 직렬화"만으로는 둘이 안 나오므로, **영속 시점에 `eps`(호출 인자)와 검증 시각(외부 시계)을 별도 주입**한다. swap-임계 필드 `no_regression`은 dataclass 필드라 영향 없음.
  2. **`retrain.json`을 version dir에 영속 (교차단계 의존 H4r, [검증 필요])** — 재학습 상세를 `gru_<fs>@<v>/retrain.json`에 기록: `epochs`·`val_loss`·`b_split_seed`·`train_pids` 요약(개수 + 해시/요약, 전체 pid 리스트는 길면 별 파일)·`b_retrain`/`b_holdout` 개수·`run_id`·`git_commit`. → 결정 6-A의 폴백(감사+meta가 아니라 *version dir의 retrain.json*)이 실제 데이터를 갖게 됨. **코드 현실 주석**: `RetrainResult`에 현재 `seed`/`run_id`/`git_commit` 필드가 없으므로(`pipeline.py:31-47`) H4r 보강 시 이 3개를 결과에 추가하거나 `materialize()` 호출부에서 주입해야 한다 — 콘솔 밖 코드 변경이라 [검증 필요].
  3. **swap 복원 경로 (콘솔 단계, 백엔드 변경 불필요)** [우리 결정]: 콘솔 승인 API는 승인 대상 버전 dir의 `validation.json`을 읽어 **`SimpleNamespace`(또는 동등 객체)로 복원**한 뒤 `deploy.swap(fs, version_dir, validation=<복원객체>, approved=True)`로 호출한다. `deploy.swap`이 `getattr(validation,"no_regression",...)`로 속성 접근을 하므로(dict가 아닌) 객체 래핑이 필요하며, 이는 콘솔 API 한 줄로 충족된다 — **백엔드 시그니처 변경 없이** in-memory 의존이 제거된다.
  4. **자재화·검증 순서 고정 + 두 파일 원자 기록 (mn-a)** [우리 결정]: 콘솔이 버전을 challenger로 인지하려면 dir에 `validation.json`*과* `retrain.json`이 **둘 다** 있어야 하므로(결정 1 보강), H4r 정규 플로우 = `retrain() → materialize()(dir 생성) → validate() → validation.json/retrain.json을 같은 dir에 기록`. **부분쓰기 방지**: 두 파일을 원자적으로 가시화한다(예: temp 경로에 쓴 뒤 `os.replace`로 rename, 또는 두 파일을 한 디렉토리 커밋 단위로 노출). 둘 중 하나라도 없는 dir은 결정 1대로 *미완성 후보*(승인 불가)로 표시 — `validation.json`만 있고 `retrain.json` 누락이면 "승인 가능하나 재학습 출처 공백"이 재발하므로 두 파일 AND로 막는다. 원자 기록의 정확한 기법(temp→rename 순서·fsync)은 구현 명세부.

- **이로써 동시에 닫히는 것**: (a) 결정 5 승인→swap이 디스크만으로 재구성 가능(복원 경로), (b) 결정 1 challenger 판별이 파일 존재로 결정, (c) 결정 4 게이트 스냅샷이 `validation.json`이라는 실데이터를 승인 시점에 손에 쥠, (d) 결정 6-A 폴백이 `retrain.json` 실데이터로 출처 공백을 메움.
- **미해결로 정직히 남기는 부분** [검증 필요]: `validation.json`/`retrain.json` 영속과 `RetrainResult`의 `seed`/`run_id`/`git_commit` 추가는 모두 **콘솔 밖 H4r 코드 변경**이다. 본 DDD는 이 영속을 교차단계 의존으로 선행 요구하며, 미선행 시 해당 버전은 *미완성 후보*로만 보이고 승인이 불가능하다(거짓 승인 금지). 직렬화 스키마 버전·`train_pids` 대용량 처리(전체 리스트 vs 해시)는 구현 명세부에서 확정.
- **검토 요청 항목**: `validation.json` 복원 객체가 `deploy.swap`의 `getattr` 접근과 정합하는지, 영속 시점(validate 직후)이 challenger 가시성 타이밍과 일치하는지.

---

## 결정 6: 실험·수치 상세는 MLflow 링크 (중복 방지)

- **결정**: "어떻게 만들었나"(피처·학습 데이터·git commit·실험 수치 비교)는 콘솔에 재구현하지 않고 **MLflow Tracking으로 링크**. 콘솔은 "운영 상태·행위"에만 집중.
- **근거 + 출처등급**: MLflow UI가 runs table·detail·git version·params/metrics를 이미 제공 [확인됨: MLflow 문서]. 재구현은 중복 [우리 결정].

### 6-A: 연결 키와 재학습 버전의 출처 (B2 — 재학습 버전에 MLflow run이 없음)

- **레드팀이 드러낸 단절** [확인됨]: 콘솔이 다루는 champion/challenger = *재학습 산출* 버전인데, 여기엔 MLflow run 자체가 없다. (1) `retrain/pipeline.py` 전체에 `mlflow` import·`start_run` 0건, `RetrainResult`에 `run_id` 없음(`pipeline.py:31-47`). (2) `deploy.materialize()`가 쓰는 `meta.json`엔 `featureset, hp, input_dim, tau, version, trained_on`만 있고 **`run_id`·git commit 없음**(`deploy.py:35-37`). → 재학습 버전 디렉토리(`gru_<fs>@<v>`)에서 MLflow run으로 잇는 키가 없다. MLflow 실험엔 H2/H3 run만 있다.
- **결정 (연결 키 확정 + 정직한 폴백)** [우리 결정]:
  1. **연결 키 = `meta.json`의 `run_id`(+`git_commit`)** — 버전 디렉토리당 1개. 콘솔은 이 키를 읽어 MLflow deep-link(`<tracking_uri>/#/experiments/.../runs/<run_id>`)를 만든다.
  2. **재학습 경로에 run_id 기록을 요구 (교차단계 의존)** — H4r의 재학습/자재화 경로가 MLflow run을 남기고 `materialize()`가 `meta.json`에 `run_id`·`git_commit`을 박도록 **보강**해야 한다. 이는 콘솔 단계가 아닌 H4r 코드 변경이므로 **교차단계 의존**으로 명시하며 [검증 필요]로 둔다(콘솔 구현 전 H4r 보강이 선행되어야 재학습 버전 링크가 성립).
  3. **H4r 보강 전까지의 폴백(정직)** [우리 결정]: `meta.json.run_id`가 있으면 MLflow 링크, **없으면 죽은 링크를 만들지 않고** 콘솔이 `meta.json`(featureset·hp·tau·trained_on)을 직접 표시한다. 더해 재학습 상세(epochs·val_loss·B-split seed·train_pids 요약)는 **version dir의 `retrain.json`**(결정 5-B / N1으로 영속됨)에서 읽어 표시하고, *승인 시점*엔 그 내용을 감사 스냅샷으로도 캡처한다(결정 4의 "게이트 결과 스냅샷"을 재학습 출처로 확장). 즉 재학습 버전의 출처는 — MLflow가 비면 — *version dir의 `validation.json`+`retrain.json`*이 1차로 보유하고, 승인 이벤트는 그 스냅샷을 감사 DB에 사본으로 남긴다.
     - **문구 한정 (mn2)**: N1 영속 결정 *이전*에는 version dir에 `meta.json` 4필드뿐이라 "감사+meta가 출처를 보유한다"는 과대 표현이었다. 폴백이 실데이터를 갖는 것은 **결정 5-B의 `validation.json`/`retrain.json` 영속이 선행될 때**에 한한다. 영속이 미선행이면 해당 버전은 결정 1대로 *미완성 후보*(승인 불가)이므로, 출처 공백이 있는 버전은 애초에 승인·운영 대상이 되지 않는다 — 공백을 덮지 않고 *대상에서 배제*하는 방식으로 닫는다.
  4. **적용 범위 명확화**: 결정 6의 깔끔한 MLflow 링크는 **run_id를 가진 버전**(H2 베이스라인, 그리고 H4r 보강 후의 재학습 버전)에 성립한다. run_id가 없는 버전은 (3)의 폴백(`validation.json`/`retrain.json` + meta.json)을 쓴다.
  5. **`/health` run_id 식별 오염 차단 (MJ2)** [확인됨: bundle.py:102 / 검증 필요]: `load_bundle_from_dir`는 `meta.get("run_id", str(d.name))`로 폴백하므로(`bundle.py:102`), 재학습 번들 meta에 `run_id`가 없으면 `/health`의 `run_id`가 alias명(`gru_vitals`)으로 표시되어 식별이 무의미해진다. (2)의 H4r 보강(`meta.json`에 `run_id`·`git_commit` 기록)이 **이 표면 식별까지 동시에 해소**한다 — `meta.json.run_id`가 채워지면 `/health`도 실제 run_id를 보고한다. H4r 보강은 콘솔 밖 코드 변경이라 [검증 필요].
- **검토 요청 항목**: 연결 키 = `meta.json.run_id` 정의가 H4r 보강과 정합하는지, run_id 부재 시 폴백(version dir 영속 파일 + meta)이 출처 공백 없이 닫는지, `/health` 식별이 run_id 기록으로 정상화되는지.

---

## 결정 7: 승인/롤백의 트랜잭션 경계 — 직렬화·권위·재기동 화해 (B-new)

- **레드팀이 드러낸 단절** [확인됨: 코드 + DDD 공백]: 콘솔 승인/롤백의 선언된 사슬 = ① version dir의 `validation.json` 읽어 복원 → ② `deploy.swap`(FS alias 전환, prev 반환) → ③ 감사 레코드 기록(게이트 스냅샷·prev) → ④ 서빙 reload 트리거. 이 4단계는 **하나의 원자 트랜잭션이 아니고 직렬화도 안 된다.** 두 가지 구멍:
  - **(면 1) 크래시 원자성 / FS·DB 분기**: ②와 ③ 사이 크래시 → alias는 새 버전인데 감사엔 swap 기록이 없다. N1 보완이 "archived = 감사 swap/rollback 이력 = source of truth"를 못 박은 결과(결정 1), 그 권위 저장소가 실제 alias 상태와 어긋나면 직전 champion이 archived로 안 보이고 게이트 스냅샷도 유실된다. 순서를 ③→②로 뒤집어도 "기록은 있는데 swap 안 됨" 창이 생긴다. `deploy.rollback`은 prev를 반환조차 안 한다(`deploy.py:68-70`).
  - **(면 2) 동시 승인 2건**: 결정 2의 "콘솔 1개면 충분"은 scale-out 얘기지 요청 동시성이 아니다. FastAPI는 한 프로세스에서 요청을 동시 처리하므로(`active_version`은 락 없는 readlink, `deploy.py:46-48`) 운영자 2명이 서로 다른 challenger를 동시 승인 가능. 둘 다 prev=`active_version()`을 V1으로 읽고 A는 V2, B는 V3로 swap → alias는 last-writer-wins로 V3에 안착하나 감사엔 `V1→V2`·`V1→V3` 두 레코드가 prev=V1로 남아 archived 도출·롤백 대상이 오염된다.

- **결정 (근본 해소)**: 승인/롤백은 **FS(alias) + DB(감사)에 걸친 트랜잭션**임을 인정하고, 세 가지를 못 박는다(기법은 명세부).
  1. **직렬화 경계 (면 2 해소)** [우리 결정]: 승인·롤백은 **featureset 단위 단일 직렬화(임계) 구간** 안에서 실행한다. `read-active(prev) → swap/set_alias → audit write`를 하나의 원자 구간으로 묶어, 동시 두 승인이 같은 prev를 읽고 갈라지는 경합을 차단한다. 직렬화 키 = **featureset**(alias가 featureset 단위이므로 — `deploy.py:46`). 동일 featureset의 승인/롤백은 순차 처리되고, 두 번째 요청은 첫 번째 커밋 후 갱신된 active를 prev로 읽거나(직렬) 충돌로 거부된다. **scale-out 의존 식별**: 콘솔이 1 프로세스인 한 프로세스-로컬 락으로 충분하지만, 다중 프로세스/replica로 가면 프로세스-로컬 락이 무력하므로 **공유 저장소 기반 락(예: 감사 DB advisory lock, alias 디렉토리 파일 lock)** 으로 승격해야 한다(결정 2 "1개면 충분"의 한정과 짝). 어느 기법(프로세스 mutex / DB advisory lock / 파일 lock)을 쓸지는 구현 명세부.
     - **경계 완전성 — 승인/롤백만이 아니라 *alias 읽기+감사 쓰기에 쓰는 모든 주체*가 경계 대상 (B-r5)** [우리 결정]: 같은 권위쌍(alias 읽기 + 감사 쓰기)에 쓰는 주체는 승인·롤백 외에도 **재기동 화해(`reconcile`)·부트스트랩 seed(`bootstrap`)** 가 있다(결정 1 mn1 시드, 결정 7-2 화해). 이 둘이 직렬화 경계 밖에 남으면 승인과 무방비 인터리브되어 동일 prev-갈라짐 버그-클래스가 재발한다(B-r5 반례). 따라서 **reconcile/seed는 콘솔이 승인/롤백 요청을 수락하기 *전에* 완료한다** — startup lifespan에서 alias↔감사 화해·seed를 끝낸 *후에야* 라우팅(승인/롤백 수락)을 개시한다. 부트스트랩(요청 수락 전)과 승인(요청 수락 후)이 시간상 상호배제되므로, reconcile/seed가 진행 중 승인이 끼어드는 인터리브가 구조적으로 불가능하다. (대안 (b) reconcile/seed도 임계구간 획득보다 (a) 부트스트랩 선완료가 단순 — 부트스트랩은 본질적으로 요청 이전 단계.) lifespan hook·라우팅 게이트의 정확한 기법은 명세부.
  2. **권위·재기동 화해 (면 1 해소)** [우리 결정]: FS alias와 감사 DB가 분기할 수 있는 창(② 후 ③ 전 크래시)에 대해 **현재 활성 상태의 권위 = FS alias**(서빙이 실제 읽는 표식)로 못 박는다. **감사 DB는 *이력(history)*의 출처지 *현재 활성 상태*의 출처가 아니다** — 결정 1의 "archived = 감사 이력"은 *과거 활성 도출*에만 유효하고, *현재 champion*은 언제나 `active_version`(alias)으로 읽는다. 분기 시 **alias가 이긴다.**
     - **재기동 화해(reconciliation)**: 콘솔 부트스트랩 시 실제 alias(`active_version`)와 감사 DB의 최신 swap/rollback 레코드를 대조한다. alias가 가리키는 버전이 감사의 마지막 활성 레코드와 다르면(=②는 됐는데 ③ 누락된 크래시 흔적) **보정 감사 레코드**(action=`reconcile`, actor=`system`, 사유=부트스트랩 화해)를 기록해 감사를 실제 상태에 맞춘다. **이 reconcile 레코드의 `prev` = 감사상 최종 활성 레코드의 버전**(target = 실제 alias가 가리키는 버전), 즉 화해가 감사를 alias 상태로 끌어올리되 `prev`엔 감사상 직전 최종 활성을 채워 **archived 도출(이전 활성→비활성 천이)이 보존**되도록 한다(결정 4의 "모든 레코드가 `prev`를 가진다" 스키마와 정합 — mn-r5). 게이트 스냅샷은 version dir의 `validation.json`에서 재읽기로 복원 가능하므로(결정 5-B) 유실되지 않고, archived 도출이 오염되지 않는다. **이 "오염 없음"이 성립하는 전제 = reconcile/seed가 승인과 상호배제(결정 7-1 경계 완전성)** — 부트스트랩 선완료로 그 전제를 못 박아 B-r5 인터리브 반례를 차단한다. (결정 1의 bootstrap seed 정책을 *화해*까지 확장.)
     - **순서 결정**: 임계 구간 내 순서 = `swap(②) → audit(③)`. ③ 실패는 재기동 화해가 마지막 안전망으로 받친다. ③→② 역순(기록 먼저)은 "기록은 있는데 swap 안 됨" 창을 만들어 alias 권위 원칙과 충돌하므로 채택하지 않는다.
  3. **prev 캡처 (mn-c)** [우리 결정]: `deploy.swap` 반환 prev(`deploy.py:57-58,63-65`)와 롤백 시 호출 *전에* 읽은 `active_version`을 **임계 구간 안에서 읽은 값 그대로 감사에 캡처**한다(롤백 대상 결정의 출처이자 경합 오염 차단). `deploy.rollback`은 prev를 반환하지 않으므로(`deploy.py:68-70`) 롤백 prev는 콘솔이 사전 읽기로 확보한다(5-A 방어 심화 권고와 일관).

- **미해결로 정직히 남기는 부분** [검증 필요]: 직렬화의 정확한 기법(프로세스 mutex vs DB advisory lock vs 파일 lock)과 락 획득 타임아웃·교착 회피, 재기동 화해의 정밀 알고리즘(부분 감사 vs 멱등 reconcile)은 **구현 명세부**에서 확정. 본 설계부는 *경계(임계 구간)·권위(alias)·복구(재기동 화해)의 존재*만 못 박는다. ②~④ 전체를 단일 DB 트랜잭션으로 감싸는 것은 FS 변이(alias 심볼릭링크 교체)가 DB 트랜잭션에 들지 않으므로 불가 — 그래서 "직렬화 + alias 권위 + 화해" 조합으로 닫는다.
- **검토 요청 항목**: 직렬화 경계가 동시 승인 2건의 prev 갈라짐을 실제로 차단하는지, alias 권위 + 재기동 화해가 ②후 ③전 크래시의 FS·DB 분기를 닫는지, prev 캡처가 롤백 대상 결정을 오염 없이 지탱하는지.

---

## 만들 것 / 안 만들 것

**만듦 (신규)**
1. 감사 저장소 (SQLAlchemy ORM, DB 교체 가능)
2. 콘솔 앱 + `/console` API (versions·validate·approve·rollback·audit — 기존 함수 노출). 승인/롤백은 **featureset 단위 직렬화 경계** 안에서 read-active→swap→audit 실행(결정 7), 부트스트랩 시 **요청 수락 전**(startup lifespan) alias↔감사 **재기동 화해·seed**(`reconcile`/`bootstrap` 감사) 완료 후 라우팅 개시(결정 7-1 경계 완전성), swap 후 **전파 확인 폴링**(`/health` run_id가 현재 `active_version`과 일치)
3. React 통합 콘솔 (champion-challenger 와이어프레임 구현)
4. Grafana 패널 2개 (G3 서빙 SLO, G4 헬스 — 메트릭은 존재, 패널만)

**안 만듦 (재활용/제외)**
- `validate.py`·`deploy.py`·`bundle.py` 함수 (그대로 노출)
- 실험 추적·수치 (MLflow 링크)
- 점진적 배포·서빙 프레임워크 (규모상 제외, ADR 참고)

---

## 성공 기준 (초안 — 검토 후 확정)

- 승인 없이는 교체 불가(`swap`의 `approved is not True` → `PermissionError`)가 API에서도 강제됨.
- **REGRESSED(=`no_regression=False`) 버전은 승인해도 비승격** — 승인 UI가 이를 반영(버튼 비활성)하고 백엔드도 `ValueError`로 거부함(M3, 이중 게이트 명시).
- **롤백도 승인 게이트 + 감사 필수**, 단 validation 재검증은 면제(과거 검증된 버전으로의 복귀) — API 경계에서 강제(M2).
- 롤백 시 모델·전처리·τ·drift reference가 함께 복원됨(번들 원자성).
- **콘솔 swap/rollback이 실행 중 서빙에 실제로 전파됨** — alias 갱신 후 (K8s 롤링 재시작 | dev `/admin/reload`)으로 `_S`/`_DS` 재로드. **전파 성공을 `/health` run_id 일치로 확인**하고, 실패 시 재시도·경보하며 확인 전엔 "전파 대기/실패"로 구분 표시 — "옛 번들 잔존 없음"이 조용히 위반되지 않음(B1/MJ-new1).
- **승인/롤백이 FS+DB 트랜잭션으로 안전하게 닫힘** — read-active→swap→audit를 featureset 단위 직렬화 구간으로 묶어 동시 승인 2건의 prev 갈라짐을 차단(B-new 면2), FS alias = 현재 활성 권위 + 재기동 화해(`reconcile` 감사)로 ②후 ③전 크래시의 FS·DB 분기를 닫음(B-new 면1), swap 반환 prev/롤백 사전 active를 감사에 캡처(mn-c).
- 모든 승인·교체·롤백이 감사 저장소에 기록됨(actor는 MVP에서 미검증 입력으로 명시 — M4).
- **게이트 결과·재학습 상세가 version dir에 영속됨** — `gru_<fs>@<v>/validation.json`(no_regression·B-holdout/A-val 수치)과 `retrain.json`(epochs·val_loss·seed·train_pids 요약·run_id·git_commit)이 디스크에 존재해, 콘솔이 in-memory 결과 없이 승인→swap·challenger 도출·게이트 스냅샷을 디스크만으로 수행함. **두 파일은 원자적으로 기록**되며 **둘 중 하나라도 없는 dir은 *미완성 후보*로 승인 배제**(N1/mn-a).
- **콘솔 swap이 디스크에서 `validation`을 복원해 호출됨** — `validation.json` → 복원 객체 → `deploy.swap(..., validation=객체, approved=True)`, 백엔드 시그니처 변경 없이 `getattr(no_regression)` 정합(N1).
- **재학습 버전의 출처가 공백 없이 추적됨** — `meta.json.run_id` 있으면 MLflow 링크, 없으면 version dir의 `validation.json`/`retrain.json`(+승인 시 감사 스냅샷) 폴백(B2/N1).
- 콘솔의 버전 표현이 `deploy.py`의 featureset 단위와 정합.
- 추론 앱과 콘솔 앱이 분리되어, 콘솔 작업이 추론 경로를 블로킹하지 않음.
- **archived 콜드스타트가 거짓 복원 없이 처리됨** — 부트스트랩 seed 감사 1건으로 현재 champion 출처 기록, 콘솔 이전 과거 이력은 비어 있음을 UI에 명시(mn1).

### 교차단계 의존 (콘솔 구현 전 선행 또는 병행 확인 — [검증 필요])
- **H4s(서빙)**: 버전 소스를 alias 기반으로 + 재로드 경로(`/admin/reload` 또는 롤링 재시작 훅) — B1. 재로드는 alias 1회 해석·`_S`/`_DS` 동시 로드·로드 중 swap 배제 규약 적용 — MJ1. **`/health`가 새 버전과 일치하는 `run_id`를 보고**해 콘솔이 전파 성공을 폴링으로 확인할 수 있어야 함 — MJ-new1(B2/MJ2의 `meta.json.run_id` 기록과 짝).
- **H4r(재학습/자재화)**: (1) MLflow run 로깅 + `meta.json`에 `run_id`·`git_commit` 기록 — B2/MJ2(`/health` 식별까지 해소). (2) **`validate()` 직후/`materialize()`가 version dir에 `validation.json`+`retrain.json`을 *원자적으로* 영속**(둘을 temp→rename 등으로 함께 가시화) — N1/mn-a. 영속 시 `eps`(호출 인자)·검증 시각은 별도 주입 — mn-b. (3) `RetrainResult`에 `seed`/`run_id`/`git_commit` 필드 추가(현재 미존재, `pipeline.py:31-47`) 또는 `materialize()` 호출부 주입 — N1. (선행 안 되면 해당 버전은 두 파일 AND 미충족으로 *미완성 후보* 승인 배제.)
- **H4r(deploy)**: `deploy.rollback`에 `approved` 가드·prev 반환 대칭화 — M2 방어 심화(권고). prev 미반환이므로 현재는 콘솔이 사전 `active_version` 읽기로 롤백 prev를 감사에 캡처 — mn-c/결정 7-3. **[구현 완료]**: 롤백 대상 archived 강제 게이트는 `service.rollback`(콘솔 경계)에 구현(BR2-1, `790a285`), `deploy.rollback` 원함수의 `approved` 가드·prev 반환 대칭화도 구현(직접 import 우회 백스톱). archived 강제 권위는 콘솔 경계 유지.
- **콘솔 내부(교차단계 아님, 본 단계 구현)**: 승인/롤백 직렬화 경계(featureset 키)·FS alias 권위·부트스트랩 재기동 화해(`reconcile` 감사) — B-new(결정 7). 기법(락 종류·화해 알고리즘)은 구현 명세부.

> **구현 명세부(어떻게)는 이 설계부가 검토를 통과한 뒤 이어붙인다** — 엔드포인트 입출력 스키마, 감사 테이블 스키마, React 컴포넌트 구조, 파일 경로.