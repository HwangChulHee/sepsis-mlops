# console-prep DDD 레드팀 검토

## 라운드 1

- 대상: design/console-prep/decisions.md (신규, 첫 검토, 설계부)
- 대상 commit: cb39acc (working tree clean)
- 검토일: 2026-06-29
- 핵심 질문: 콘솔(design/console/decisions.md, 통과)이 백엔드에 거는 교차단계 의존 6종을 이 문서가 실제로·끝까지 닫는가
- 판정: HOLD — blocker 1건 (major 2 / minor 4)

코드 대조 결과 본문의 출처표기(deploy.py:46/55/61/68, validate.py:46/60, bundle.py:102, app.py:34-44/97-110, pipeline.py:31-47/50)는 모두 실제 코드와 일치한다. 문제는 표기 정확성이 아니라 콘솔이 명시적으로 요구한 백엔드 의존 일부가 결정으로 닫히지 않은 것이다.

### PASS
- 결정 1 (validation.json 영속) — 핵심 swap 사슬이 닫힘. validate()가 in-memory ValidationResult만 반환하고 디스크에 안 씀(validate.py:46,60 확인), swap은 getattr(validation,"no_regression",False)를 요구함(deploy.py:55,61 확인). 디스크에 no_regression 포함 영속 → 콘솔이 복원 객체로 swap 호출 → 사슬 성립. eps·검증시각이 ValidationResult 필드가 아니라 별도 주입 필요함도 정확히 식별(validate.py:31-43,46 확인).
- 결정 4 (meta.json run_id) — MJ2 표면식별 오염 해소. load_bundle_from_dir가 meta.get("run_id", str(d.name)) 폴백(bundle.py:102 확인)이라 재학습 번들의 /health.run_id가 alias명으로 오염됨이 사실. meta.json에 run_id 기록이 이를 닫음.
- 결정 5 (서빙 alias 소스 + reload) — B1 단절 해소. state()가 _S에 1회 로드 후 영구 캐시·재로드 트리거 없음(app.py:34-44 확인), 컨테이너가 고정 SERVE_BUNDLE_DIR만 읽음(app.py:38-40 확인). alias 소스화 + /admin/reload/롤링 재시작이 실행 중 pod 전파를 닫음.
- 결정 6 (reload 원자성) — MJ1 스큐 방지. _S/_DS 분리 lazy-load(app.py:34-44,97-110 확인)로 인한 모델↔reference 버전 스큐를 alias 1회 해석·동시 로드·로드 중 swap 배제로 닫음. 락 기법을 명세부로 미룬 깊이 구분도 적절.
- 누수 불변 보존. 결정 1~6은 영속·reload만 건드리고 환자단위 B 분할·train-only stats·0-fill 금지·mask OFF를 변경하지 않음(pipeline.py:61-77 확인). 성공 기준 line 102가 이를 명시.

### blocker
#### B1 — validation.json·retrain.json의 원자·완전(co-visible) 영속 결정 누락 (콘솔이 명시적으로 의존하는 백엔드 계약이 닫히지 않음)
- 문제: 콘솔은 challenger 판별·감사 스냅샷 무결성을 "두 파일이 원자적으로 함께 가시화된다(부분/torn write 없음)" 전제 위에 세운다. 콘솔 결정 5-B(mn-a, line 139)와 교차단계 의존(line 218): "version dir에 validation.json+retrain.json을 원자적으로 영속(둘을 temp→rename 등으로 함께 가시화) — N1/mn-a" 가 백엔드에 건 명시적 의존이다. 그런데 console-prep은 결정 1(validation.json)·결정 2(retrain.json)를 독립 결정으로만 두고, 두 파일의 원자 co-visible 기록도, torn-read 방지도, "두 파일 AND가 완성 표식"이라는 결정도 어디에도 없다.
- 근거(흐름 추적+코드): 영속 타이밍이 두 파일에서 갈린다 — retrain.json 데이터는 materialize(retrain_result, version) 시점에 손에 있음(RetrainResult에 epochs/val_loss/train_pids 등 존재, pipeline.py:42-47). validation.json은 validate()가 끝난 이후에만 가능(validate.py:46, materialize 이후 실행). → retrain.json은 일찍, validation.json은 늦게 써지므로 "둘 중 하나만 있는" 창이 구조적으로 생긴다. 콘솔 AND-게이트가 결측 케이스는 막지만, torn/half-written 파일(둘 다 존재하나 하나가 잘림)은 못 막는다 — 콘솔이 challenger로 인지 → 깨진 JSON을 감사 스냅샷(결정 4)·swap 복원에 사용. console-prep 성공 기준(line 99)은 validation.json 없는 dir만 미완성 후보로 배제한다고 적어, 콘솔의 두 파일 AND 완성 기준(콘솔 결정 1 line 40, 5-B line 139)과 직접 불일치. retrain.json만 빠진 dir 처리가 console-prep엔 없다.
- 설계부 사안인 이유: 기법(temp→rename·fsync 순서)은 명세부가 맞다. 그러나 "두 파일이 원자적·완전하게 영속돼야 한다는 의존 식별 자체"는 설계부 몫이다. 콘솔은 이를 설계 결정(mn-a)으로 다뤘는데 console-prep은 결정으로도 명시적 명세부 위임으로도 남기지 않았다 — 단어조차 없다.
- 제안: 결정 1·2에(또는 신설 결정으로) "validation.json과 retrain.json은 한 커밋 단위로 원자·co-visible하게 가시화하며, 완성 표식 = 두 파일 AND, 부분/torn write는 미완성 후보로 배제"를 의존으로 못 박아라(기법은 명세부 위임 명시). 성공 기준 line 99의 단일-파일 기준을 두 파일 AND로 교정.

> **[reviser 응답]** 해소: 신설 **결정 7 「validation.json·retrain.json의 원자·co-visible 영속 (완성 표식 = 두 파일 AND)」** 을 추가(decisions.md:88-103). 두 파일을 한 커밋 단위로 원자·co-visible하게 가시화, 완성 표식 = 두 파일 AND, 부분/torn write는 미완성 후보로 배제를 **백엔드 계약(교차단계 의존)으로 못 박음**. 영속 타이밍 갈림(retrain=materialize 시점·early, validation=validate 이후·late)을 근거로 명시하고, torn-read(둘 다 존재하나 하나 잘림)까지 미완성 후보로 배제. 기법(temp→rename·fsync 순서·디렉토리 커밋 단위)은 **명세부 위임**으로 명시(설계부 깊이 유지). 성공 기준의 단일-파일 기준(구 line 99)을 **두 파일 AND**로 교정(decisions.md:117). 결정 1·2 본문에도 결정 7 상호참조 추가(decisions.md:28,45).

### major
#### MJ1 — b_split_seed 데이터 출처가 잘못 식별됨 (코드 모순 + 통과한 콘솔 문서와 불일치)
- 문제: 결정 2(line 38,42-43)는 retrain.json에 b_split_seed를 기록하며 근거로 "seed는 retrain(seed=42) 인자로 존재 [확인됨: pipeline.py:50] → b_split_seed로 기록 가능. run_id·git_commit만 신규(결정 3)." 라 한다. 그러나 seed는 함수 인자일 뿐 RetrainResult 필드가 아니다(pipeline.py:31-47 — seed 없음). 영속 지점인 materialize(retrain_result, version)은 retrain_result만 받으므로(deploy.py:28) seed에 도달할 수 없다. 결정 3은 run_id·git_commit만 RetrainResult/materialize에 주입하고 seed는 다루지 않는다. → seed가 어느 결정으로도 영속 지점에 전달되지 않는다.
- 콘솔과의 불일치: 통과한 콘솔 결정 5-B(line 137)는 "RetrainResult에 현재 seed/run_id/git_commit 필드가 없으므로 ... 이 3개를 결과에 추가하거나 materialize() 호출부에서 주입해야 한다"고 정확히 식별했다. console-prep은 그보다 부정확하게 seed를 "이미 있음"으로 처리해, 자신이 지탱해야 할 상위 문서와 모순된다.
- 제안: 결정 3(또는 결정 2)에서 seed도 run_id·git_commit과 동일하게 RetrainResult에 추가하거나 materialize 인자로 주입하는 의존으로 명시. "retrain(seed=42) 인자로 존재 → 기록 가능"이라는 근거를 코드 현실(영속 지점 미도달)에 맞게 교정.

> **[reviser 응답]** 해소: 코드 재확인(pipeline.py:31-47에 seed 필드 없음, materialize는 retrain_result만 받음 deploy.py:28) 결과 지적이 정확. 결정 2의 근거를 **"seed는 retrain() 함수 인자일 뿐 RetrainResult 필드가 아니고, 영속 지점 materialize(retrain_result, version)은 retrain_result만 받아 seed에 도달 불가 → run_id·git_commit과 동일하게 결정 3으로 전달 필요"** 로 코드 현실에 맞게 교정(decisions.md:42). 결정 3의 주입 대상을 `run_id`·`git_commit`·**`seed`** 3종으로 확장하고 콘솔 결정 5-B(line 137)와 일치시킴(decisions.md:51,54-56). 허위 "이미 있음" 표현 제거.

#### MJ2 — 전파 확인의 식별자 네임스페이스 미스매치가 사슬 마지막 홉에 남음
- 문제: 콘솔의 전파 성공 확인(MJ-new1, 콘솔 결정 2-A line 66 / 성공기준 line 206)은 "서빙 /health의 run_id가 현재 active_version(alias가 가리키는 버전)과 일치하는지 폴링"이다. 그러나 코드상 두 값은 다른 네임스페이스다: active_version()은 os.readlink = 버전 디렉토리명(gru_vitals@<v>)을 반환(deploy.py:46-48), /health.run_id는 meta.json의 MLflow run_id 해시를 반환(app.py:80, 결정 3·4). 둘은 결코 같지 않다. console-prep 결정 4는 run_id를 제공하지만, 콘솔이 비교해야 할 대상이 active_version 문자열이 아니라 그 swap이 쓴 타겟 dir의 meta.json.run_id임을 어디에도 명시하지 않는다.
- 근거: 콘솔의 교차단계 의존(line 217)이 "/health가 새 버전과 일치하는 run_id를 보고"를 백엔드(H4s)에 요구 → console-prep이 답해야 할 사안. 데이터(meta.json.run_id)는 결정 4로 존재하나, 비교 의미(타겟 dir의 run_id ↔ /health.run_id)가 명시되지 않아 사슬 마지막 홉이 느슨하다.
- 제안: 결정 4(또는 결정 5)에 "전파 확인은 /health.run_id를 타겟 버전 dir의 meta.json.run_id와 비교한다(active_version 문자열이 아님)"를 한 줄로 못 박아 식별자 네임스페이스를 일치시켜라. (콘솔 결정 2-A의 "active_version과 일치" 문구도 이 의미로 읽혀야 함.)

> **[reviser 응답]** 해소: 결정 4에 **「전파 확인의 비교 대상 (식별자 네임스페이스 일치)」** 절을 추가(decisions.md:66-68). active_version()은 os.readlink로 *버전 디렉토리명*(deploy.py:46-48)을, /health.run_id는 *meta.json의 run_id 해시*(app.py:80)를 반환해 두 네임스페이스가 다름을 명시하고, **전파 확인 = /health.run_id ↔ 현재 alias가 가리키는 타겟 버전 dir의 meta.json.run_id 비교**(active_version 문자열이 아님)로 못 박음. 콘솔 결정 2-A "active_version과 일치" 문구도 이 의미(현재 alias 타겟 dir의 meta.json.run_id)로 읽힘을 명시.

### minor
- mn1 — validation.json 필드 괄호 목록이 핵심 수치를 누락. 결정 1(line 27)은 "ValidationResult 전체"라 하지만 괄호 예시가 new_aval_util/old_aval_util/new_aval_prauc/old_aval_prauc를 빠뜨린다. 이들이 콘솔 결정 3이 "주인공으로 크게" 띄우는 무회귀 헤드라인 수치(validate.py:34-38)다. "전체"가 지배하므로 데이터 누락은 아니나 명시 권장.

> **[reviser 응답]** 해소: 결정 1 괄호 목록에 `new_aval_util`·`old_aval_util`·`new_aval_prauc`·`old_aval_prauc`(콘솔 결정 3 무회귀 헤드라인 수치)를 추가(decisions.md:27).

- mn2 — run_id 단일 출처 미확정. 결정 2·4가 retrain.json·meta.json 양쪽에 run_id를 둠. 콘솔 결정 6-A는 연결 키=meta.json.run_id로 확정했으므로, retrain.json의 run_id는 사본/감사용임을 명시해 단일 출처를 못 박아라.

> **[reviser 응답]** 해소: 결정 4에 **연결 키(MLflow deep-link)의 단일 출처 = `meta.json.run_id`**, retrain.json의 run_id는 **감사용 사본**임을 명시(decisions.md:64). 콘솔 결정 6-A(연결 키=meta.json.run_id)와 정합.

- mn3 — dev MLflow 폴백 경로 대체가 미언급. 결정 5가 서빙 소스를 alias로 통일하면 현재 dev 경로 load_bundle(SERVE_FEATURESET)=MLflow h2 experiment by featureset(app.py:42, bundle.py:105-138)이 대체된다. 의도된 통일이나 기존 dev 폴백이 superseded됨을 결정 5에 명시하지 않아 구현 시 혼선 여지.

> **[reviser 응답]** 해소: 결정 5에 **기존 dev MLflow 폴백 경로(load_bundle(SERVE_FEATURESET) = MLflow h2 by featureset, app.py:42)가 alias 통일로 superseded**됨을 명시(decisions.md:77). 의도된 대체임을 적어 구현 혼선 차단.

- mn4 — 인용 미세 오류 + rollback 대칭화 미반영. (a) 결정 2(line 41)의 [확인됨: pipeline.py:42-47]은 b_retrain/b_holdout/train_pids에는 부정확 — 실제 위치는 pipeline.py:39-41. 주장은 맞으나 줄 범위 교정 권장. (b) 콘솔이 백엔드 권고로 남긴 deploy.rollback의 approved 가드·prev 반환 대칭화(콘솔 line 219, deploy.py:68-70 prev 미반환 확인)가 console-prep엔 없음. 콘솔이 사전 active_version 읽기로 우회하므로 비블로킹이나 권고를 함께 실어두면 일관.

> **[reviser 응답]** 해소: (a) 코드 재확인 결과 b_retrain/b_holdout/train_pids는 pipeline.py:39-41(epochs/val_loss는 45-46), 줄 범위를 **pipeline.py:39-47**로 교정(decisions.md:41). (b) 결정 5(또는 신규)로 두지 않고, 콘솔이 이미 권고로 보유(콘솔 line 219·5-A·결정 7-3)하고 console-prep은 **순수 백엔드 영속 보강 범위**라 결정 3 근처에 **deploy.rollback의 approved 가드·prev 반환 대칭화는 H4r deploy 권고로 콘솔 문서가 보유함**을 교차참조로 한 줄 명시(decisions.md:56)해 일관 유지.

### 종합
핵심 swap 사슬(결정 1)·서빙 전파(결정 5·6)·식별 오염(결정 4)은 코드 대조상 닫힌다. 그러나 콘솔이 백엔드에 명시적으로 건 두 파일 원자·완전 영속 의존(mn-a / 교차단계 의존 line 218)이 결정으로도 명세부 위임으로도 부재하여(B1) 콘솔의 완성-기준·감사 무결성 전제가 백엔드에서 지탱되지 않는다. 추가로 seed 출처 오식별(MJ1)은 통과한 상위 콘솔 문서보다 부정확하고, 전파 확인 식별자 미스매치(MJ2)가 사슬 마지막 홉에 남는다. blocker 1건이므로 HOLD.

> **[reviser 종합 응답]** 라운드 1 보완: blocker B1(신설 결정 7 — 두 파일 원자 co-visible 영속·AND 완성 표식, 성공 기준 교정) + major 2건(MJ1 seed 출처를 코드 현실·콘솔 5-B와 일치, MJ2 전파 확인 식별자 네임스페이스 일치) + minor 4건(mn1 헤드라인 수치 명시, mn2 run_id 단일 출처, mn3 dev 폴백 superseded, mn4 줄범위 교정·rollback 대칭화 교차참조) 모두 반영. 기법은 일관되게 명세부로 위임(설계부 깊이 유지).
