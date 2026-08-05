# EC2 인프라 스택 상세 설명 (`infra-pr.yaml` / `infra-dev.yaml`)

CloudFormation 템플릿 하나로 **이미지 빌드 → 저장 → EC2 배포 → 자동 HTTPS →
자동 재배포 → 시작/정지 스케줄**까지 전부 만든다. 실행 방법은 `RUNBOOK.md` 참고.
이 문서는 "이 yaml이 무엇을, 왜 만드는가"를 설명한다.

## 두 템플릿 — 외부(pr) / temp 포함(dev)

구조·접근 방식은 **동일**하고, `temp`(아직 비공식) 엔드포인트를 번들에 넣느냐만 다르다.
둘 다 `0.0.0.0/0`로 열려 어디서든 접근 가능하며 OAuth로 보호된다.

| | `infra-pr.yaml` (외부·공개) | `infra-dev.yaml` (temp 포함) |
| --- | --- | --- |
| `IncludeTemp` | `false` — `temp` 스키마·`midas_temp` tool 모두 제외 | `true` — `temp` 포함 |
| `EcrRepositoryName` | `midas-mcp` | `midas-mcp-dev` (한 계정에 공존하도록 분리) |
| 접근 제어 | 80·443 모두 `AllowedCidr`(기본 `0.0.0.0/0`) | 80·443 모두 `AllowedCidr`(기본 `0.0.0.0/0`) — pr과 동일 |

> dev도 개방돼 있으므로 비공식 `temp`는 **인증만 통과하면 외부에서도 닿는다.** 노출을
> 원치 않으면 dev URL을 내부에만 배포하거나 `AllowedCidr`를 좁힌다(단 80까지 좁혀져
> Let's Encrypt가 깨질 수 있음 — pr과 동일 제약).

`temp`를 실제로 가르는 것은 **이미지 안에 `data/schemas/temp` 파일이 있느냐** 하나다:
빌드 시 `--build-arg INCLUDE_TEMP`가 false면 `Dockerfile`이 그 폴더를 지우고, 그러면
카탈로그에서 사라질 뿐 아니라 `server.py`가 `midas_temp` tool 등록도 건너뛴다
(`catalog.has_group("temp")` 기준). 별도 런타임 ENV는 없다. 로컬 `.mcpb` 번들도
같은 방식이다(`mcpb/build.ps1 -IncludeTemp`, `mcpb/README.md`).

> 두 스택은 **호스트명(`ServiceHostname`)이 서로 달라야** 한다 — EIP·인증서가 스택마다
> 따로라 같은 FQDN을 공유할 수 없다. 경로(`/mcp`)는 둘 다 그대로 둔다.

## 전체 그림

```
 GitHub (public)
   │  git push          ← 이미지를 갱신하는 유일한 행위
   ▼
 CodeBuild (ARM 네이티브, QEMU 없음)
   │  docker push
   ▼
 ECR :latest
   │  ECR Image Action (PUSH, SUCCESS)
   ▼
 EventBridge Rule → SSM RunCommand: /opt/redeploy.sh
   ▼
 EC2 t4g.small
   ├─ midas : MCP 서버 :8080 (내부)
   └─ caddy : 80/443 자동 HTTPS → midas:8080
```

핵심 설계: **재배포 트리거는 CodeBuild가 아니라 ECR의 push 이벤트**다. 조건은
«저장소 이름 + 감시 태그 + PUSH 성공»뿐이라, 누가 어떻게 올렸든(CodeBuild / 로컬
`docker push` / 콘솔 / 태그 재부착) 자동 반영된다.

## 파라미터 (Parameters)

**반드시 지정** — 기본값이 없어 빠지면 배포 실패.

| 파라미터 | 무엇을 넣나 |
| --- | --- |
| `ServiceHostname` | 클라이언트가 접속할 FQDN(예: `mcp.example.com`). **실제로 소유한 도메인**이어야 한다 — Caddy가 이 이름으로 인증서를 받고, 이 이름의 A 레코드가 Elastic IP를 가리켜야 한다. **pr·dev는 서로 다른 FQDN**을 써야 한다. |
| `AcmeEmail` | Let's Encrypt 계정 이메일. 인증서 만료 경고가 여기로 온다. 받을 수 있는 실제 주소. |

**기본값 있음** — 필요할 때만 덮어쓴다.

| 파라미터 | 기본값 | 언제 바꾸나 |
| --- | --- | --- |
| `GitHubBranch` | `master` | **빌드할 브랜치.** 기본값이 아니면 반드시 지정. 잘못 두면 엉뚱한 코드가 배포된다. |
| `GitHubRepoUrl` | 이 리포 | 포크했거나 리포를 옮겼을 때 |
| `GitHubToken` | 비어 있음 | push마다 자동 빌드(웹훅)를 원할 때만 |
| `IncludeTemp` | pr `false` / dev `true` | `temp` 엔드포인트 번들 여부. **파일별 기본값을 그대로 쓰는 게 정상** — 뒤집으면 외부에 비공식 API가 노출되거나 사내에서 사라진다 |
| `EcrRepositoryName` | pr `midas-mcp` / dev `midas-mcp-dev` | 계정에 같은 이름 저장소가 이미 있을 때(있으면 생성 실패). 두 스택이 서로 다른 기본값을 쓰는 이유 |
| `ImageTag` | `latest` | 감시할 태그. 이 태그로 push될 때만 재배포가 걸린다 |
| `InstanceType` | `t4g.small` | 메모리 부족 시 `t4g.medium`. **ARM(`t4g.*`) 유지 필수** — CodeBuild가 arm64로 빌드한다 |
| `VolumeSizeGb` | `20` | 이미지·로그가 쌓여 디스크가 모자랄 때 |
| `AllowedCidr` | `0.0.0.0/0` (pr·dev 동일) | 80·443 접근 허용 대역. 둘 다 기본 전체 개방이라 생략 가능. 좁히려면 CIDR를 넣되 80까지 함께 좁혀져 Let's Encrypt HTTP-01이 깨질 수 있다(그 경우 DNS 챌린지로 전환) |
| `StartCron` / `StopCron` | 매일 08:00 / 24:00(자정) | 운영 시간이 다를 때. 24시간 가동하려면 배포 후 두 스케줄 비활성화 |
| `ScheduleTimezone` | `Asia/Tokyo` | 한국 기준이면 `Asia/Seoul` |
| `LatestAmiId` | AL2023 ARM64 | 건드리지 않는다 |

## 리소스 (Resources) — 기능별 그룹

### 이미지 저장·빌드
| 논리 ID | 타입 | 역할 |
| --- | --- | --- |
| `EcrRepository` | `AWS::ECR::Repository` | 이미지 저장소. `EmptyOnDelete: true`라 스택 삭제 시 이미지째 삭제 |
| `BuildRole` | `AWS::IAM::Role` | CodeBuild가 ECR push·로그 기록에 쓰는 권한 |
| `GitHubCredential` | `AWS::CodeBuild::SourceCredential` | GitHub 자격증명(토큰). **계정+리전당 1개**만 저장됨 |
| `BuildProject` | `AWS::CodeBuild::Project` | ARM 네이티브로 `Dockerfile`을 빌드해 ECR로 push. `INCLUDE_TEMP` 환경변수를 `docker build --build-arg`로 넘겨 `temp` 포함 여부를 결정 |

### 첫 빌드 자동 실행
| 논리 ID | 타입 | 역할 |
| --- | --- | --- |
| `InitialBuildRole` | `AWS::IAM::Role` | 아래 Lambda 실행 권한 |
| `InitialBuildFunction` | `AWS::Lambda::Function` | 스택 생성 시 `start-build`를 한 번 호출 |
| `InitialBuild` | `AWS::CloudFormation::CustomResource` | 위 Lambda를 트리거 — 배포 명령 하나로 **첫 빌드까지** 자동으로 굴러가게 함 |

### 서버 실행 (EC2 + HTTPS)
| 논리 ID | 타입 | 역할 |
| --- | --- | --- |
| `InstanceRole` | `AWS::IAM::Role` | EC2가 ECR pull·SSM에 쓰는 권한 |
| `InstanceProfile` | `AWS::IAM::InstanceProfile` | 위 롤을 인스턴스에 붙이는 껍데기 |
| `SecurityGroup` | `AWS::EC2::SecurityGroup` | pr·dev 동일 — 80·443을 `AllowedCidr`(기본 `0.0.0.0/0`)에 개방 |
| `Eip` / `EipAssociation` | `AWS::EC2::EIP` | 고정 IP — 정지/시작해도 주소 불변 (A 레코드 안정) |
| `Instance` | `AWS::EC2::Instance` | 실제 서버. user-data가 Docker·Caddy·재배포 유닛을 깐다(아래) |

### 자동 재배포
| 논리 ID | 타입 | 역할 |
| --- | --- | --- |
| `EcrPushInvokeRole` | `AWS::IAM::Role` | EventBridge가 SSM RunCommand를 부를 권한 |
| `EcrPushRule` | `AWS::Events::Rule` | «ECR push 성공» 이벤트 → `/opt/redeploy.sh` 실행 |

### 시작/정지 스케줄
| 논리 ID | 타입 | 역할 |
| --- | --- | --- |
| `SchedulerRole` | `AWS::IAM::Role` | EC2 start/stop 권한 |
| `StartSchedule` / `StopSchedule` | `AWS::Scheduler::Schedule` | 평일 08:00 시작 / 20:00 정지 (기본 `Asia/Tokyo`) |

## Instance user-data — 부팅 시 **1회만** 실행

`Instance`의 user-data가 인스턴스 생애 1회 아래 셋을 깔고 다시 안 건드린다:

| 설치물 | 내용 |
| --- | --- |
| `/opt/midas.env` | CloudFormation만 아는 값들(리전·ECR 주소·이미지 URI·`PUBLIC_URL`). `PUBLIC_URL=https://${ServiceHostname}`이 들어 있어 **OAuth가 켜진 채로 배포**된다. 헤더 방식으로 되돌리려면 이 줄과 아래 `docker run`의 `-e`를 지운다 (`auth/README.md`) |
| `/opt/redeploy.sh` | ECR 로그인 → pull → 컨테이너 교체. 부팅 시와 ECR push 시 양쪽에서 호출되는 단일 스크립트 |
| `midas-redeploy.service` | 부팅마다 위 스크립트를 실행, **실패 시 60초마다 재시도** |

> **self-healing**: 첫 배포에서 EC2가 빌드보다 먼저 떠도, 60초 재시도가 빌드 완료
> 후 이미지를 알아서 물어온다. 그래서 부팅 직후 `docker ps`에 `midas`가 없어도 정상.

Caddy는 앱보다 먼저 뜬다(의도적): `midas` 업스트림을 요청마다 해석하므로, 첫 빌드
동안 인증서를 받아둘 수 있다(앱이 뜨기 전엔 502).

> **user-data는 1회성**이라, `redeploy.sh`나 `midas.env`를 바꾸려면 **인스턴스 교체
> (스택 재생성)** 가 필요하다. 이미지 재배포만으로는 반영되지 않는다.

## 출력 (Outputs)

| 출력 | 용도 |
| --- | --- |
| `McpEndpoint` | 최종 접속 URL (`https://<도메인>/mcp`) |
| `DnsRecordToCreate` | 만들어야 할 A 레코드 (`<도메인> A <ElasticIP>`) |
| `ElasticIP` | 고정 IP 값 |
| `RebuildCommand` | 수동 재빌드 명령 (`codebuild start-build ...`) |
| `ForceRedeploy` | 재빌드 없이 현재 이미지 재-pull |
| `BuildLogs` | CodeBuild 콘솔 링크 |
| `ImageUri` | 현재 이미지 URI |
| `SsmConnect` | 인스턴스 셸 접속 명령 |

## 명령어 옵션 (배포 명령의 플래그)

| 옵션 | 의미 | 바꾸나 |
| --- | --- | --- |
| `--region` | 전부 이 리전에 생성. **도쿄 `ap-northeast-1`로 통일.** 콘솔·CloudShell도 같은 리전 | 그대로 |
| `--template-file` | 업로드한 파일 경로(홈에 떨어지므로 파일명만) | 그대로 |
| `--stack-name` | 스택 이름 = CodeBuild 프로젝트 접두사. 같은 이름 재실행은 **업데이트** | 병행 환경이면 `-stg` 등 |
| `--capabilities CAPABILITY_IAM` | IAM 롤 생성 승인. 없으면 거부 | 필수 |

> `--parameter-overrides`는 **생략한 파라미터를 기본값으로 되돌린다.** 업데이트 시
> 이전 값을 매번 다시 쓰거나, 유지하려면 `ParameterKey=...,UsePreviousValue=true`.

## 설계 노트

- **아키텍처(ARM)**: CodeBuild가 arm64 네이티브로 빌드하므로 인스턴스는 `t4g.*` 유지.
  x86(`t3.*`)으로 바꾸려면 `Environment.Type`을 `LINUX_CONTAINER`+x86 이미지로 함께.
- **리포 public**: CodeBuild가 자격증명 없이 클론 가능한 이유. private으로 바꾸면
  클론에도 `GitHubToken` 필요.
- **인증(OAuth)**: `midas.env`의 `PUBLIC_URL`을 `docker run`이 `-e MIDAS_MCP_PUBLIC_URL`로
  넘겨 **OAuth가 켜진 채 배포**된다. 토큰 저장소는 `-v midas_data:/data` 볼륨에 영속돼
  이미지 교체에도 인증이 유지된다(인스턴스 교체 시엔 재인증). 클라이언트는 `/mcp`에
  토큰 없이 접속하면 `401`을 받고 로그인 폼으로 유도된다 → `midas_mcp/auth/README.md`.
- **Dockerfile은 리포에 있어야** CodeBuild가 클론해 빌드한다(`Dockerfile`,
  `.dockerignore`, `deploy/`를 커밋).
- **temp 게이팅**: `IncludeTemp` 파라미터 → CodeBuild `INCLUDE_TEMP` 환경변수 →
  `docker build --build-arg INCLUDE_TEMP=...`. `Dockerfile`은 false일 때 `pip install`
  **전에** `data/schemas/temp`를 지워 wheel 번들에서 제외한다. 파일이 없으면 카탈로그에도
  안 뜨고 `server.py`가 `midas_temp` 등록도 건너뛴다 → 발견·호출 표면 모두 사라짐.
  `IncludeTemp`는 `InitialBuild` 입력에도 물려 있어 값 변경 시 스택 업데이트가 재빌드를 건다.
