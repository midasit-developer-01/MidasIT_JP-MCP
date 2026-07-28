# 배포 런북 — CloudShell + CodeBuild + ECR (`infra-ec2.yaml`)

**로컬에 설치할 것이 없다.** AWS CLI도, Docker도 필요 없다. CloudShell에 yaml
하나 올리고 명령 하나 실행하면 빌드까지 알아서 굴러간다.

```
 GitHub (public)
   |  git push          ← 이미지를 갱신하는 유일한 행위
   v
 CodeBuild (ARM 네이티브, QEMU 없음)
   |  docker push
   v
 ECR :latest
   |  ECR Image Action (PUSH, SUCCESS)
   v
 EventBridge Rule → SSM RunCommand: /opt/redeploy.sh
   v
 EC2 t4g.small
   ├─ midas : MCP 서버 :8080 (내부)
   └─ caddy : 80/443 자동 HTTPS → midas:8080
```

## 1. 배포 — CloudShell에서 이것만

CloudShell 우상단 **Actions → Upload file** 로 `deploy/infra-ec2.yaml` 업로드 후:

```bash
aws cloudformation deploy \
  --region ap-northeast-2 \
  --template-file infra-ec2.yaml \
  --stack-name midas-mcp \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      GitHubBranch=Deploy-Image \
      ZoneDomain=example.com \
      ServiceHostname=mcp.example.com \
      AcmeEmail=you@example.com
```

이 한 줄이 ECR 저장소, CodeBuild 프로젝트, **첫 빌드 실행**, EC2, Caddy,
Elastic IP, Route 53 존, 재배포 규칙, 시작/정지 스케줄까지 전부 만든다.
빌드는 3~5분 걸리고, 끝나는 순간 EC2가 이미지를 받아 컨테이너를 띄운다.

### 명령어 옵션

| 옵션 | 무엇인가 | 바꿔야 하나 |
| --- | --- | --- |
| `--region` | 전부 이 리전에 생성된다. 나중에 옮기려면 스택을 다시 만들어야 한다. | 서울=`ap-northeast-2`, 도쿄=`ap-northeast-1`. 사용자와 가까운 쪽. |
| `--template-file` | CloudShell에 업로드한 파일 경로. 업로드는 홈 디렉터리에 떨어지므로 파일명만 쓰면 된다. | 그대로 |
| `--stack-name` | 스택 이름이자 CodeBuild 프로젝트 이름의 접두사(`<이름>-build`). 같은 이름으로 다시 실행하면 **신규 생성이 아니라 업데이트**다. | 그대로. 별도 환경을 병행하려면 `midas-mcp-stg` 식으로. |
| `--capabilities CAPABILITY_IAM` | 이 템플릿이 IAM 롤을 만든다는 명시적 승인. 없으면 거부된다. | 필수, 그대로 |

### 파라미터 (`--parameter-overrides`)

**반드시 지정해야 하는 것** — 기본값이 없어서 빠지면 배포가 실패한다.

| 파라미터 | 무엇을 넣나 |
| --- | --- |
| `ZoneDomain` | **실제로 소유한** 등록 가능 도메인. Route 53 호스팅 존이 이 이름으로 만들어진다. `example.com` 형식(`www.`나 뒤 점 없이, 서브도메인 아님). 소유하지 않은 도메인을 넣으면 NS를 넘길 수 없어 인증서가 영원히 발급되지 않는다. |
| `ServiceHostname` | 클라이언트가 접속할 FQDN. 보통 `mcp.<ZoneDomain>`. 반드시 `ZoneDomain` 안에 속해야 하며(A 레코드를 그 존에 만들기 때문), apex 자체(`ZoneDomain`과 동일)로 둬도 된다. Caddy가 이 이름으로 인증서를 받는다. |
| `AcmeEmail` | Let's Encrypt 계정 이메일. 인증서 만료 경고가 여기로 온다. 받을 수 있는 실제 주소. |

**기본값이 있어 보통 생략하는 것** — 필요할 때만 덮어쓴다.

| 파라미터 | 기본값 | 언제 바꾸나 |
| --- | --- | --- |
| `GitHubBranch` | `master` | **빌드할 브랜치.** 기본값이 아니면 반드시 지정. 잘못 두면 엉뚱한 코드가 배포된다. |
| `GitHubRepoUrl` | 이 리포 | 포크했거나 리포를 옮겼을 때 |
| `GitHubToken` | 비어 있음 | push마다 자동 빌드(웹훅)를 원할 때만. 3-A 참고 |
| `EcrRepositoryName` | `midas-mcp` | 계정에 같은 이름 저장소가 이미 있을 때(있으면 생성 실패) |
| `ImageTag` | `latest` | 감시할 태그. 이 태그로 push될 때만 재배포가 걸린다 |
| `InstanceType` | `t4g.small` | 메모리가 부족할 때 `t4g.medium`. **ARM(`t4g.*`) 유지 필수** — CodeBuild가 arm64로 빌드한다 |
| `VolumeSizeGb` | `20` | 이미지·로그가 쌓여 디스크가 모자랄 때 |
| `AllowedCidr` | `0.0.0.0/0` | Let's Encrypt HTTP-01이 외부 접근을 요구하므로 **초기엔 그대로 둬야 한다.** 인증서 발급 후 좁히려면 DNS 챌린지로 바꿔야 함 |
| `StartCron` / `StopCron` | 평일 08:00 / 20:00 | 운영 시간이 다를 때. 24시간 가동하려면 배포 후 두 스케줄을 비활성화 |
| `ScheduleTimezone` | `Asia/Seoul` | 일본 기준이면 `Asia/Tokyo` |
| `LatestAmiId` | AL2023 ARM64 | 건드리지 않는다 |

> `--parameter-overrides`는 **생략한 파라미터를 기본값으로 되돌린다.** 스택을
> 업데이트할 때는 이전에 넘겼던 값을 매번 전부 다시 써주거나, 유지하려면
> 해당 항목에 `ParameterKey=...,UsePreviousValue=true`를 쓴다.

출력값:
```bash
aws cloudformation describe-stacks --region ap-northeast-2 \
  --stack-name midas-mcp --query "Stacks[0].Outputs" --output table
```

## 2. 도메인 연결 (자동화 불가, 최초 1회)

`NameServers` 출력의 4개를 도메인 등록기관의 NS 레코드로 설정한다. 전파되면
Caddy가 Let's Encrypt 인증서를 자동 발급한다(계속 재시도하므로 기다리면 됨).

## 3. 이미지 갱신

재배포 트리거는 CodeBuild가 아니라 **ECR의 push 이벤트**다. 조건은 «저장소
이름 + 감시 태그 + PUSH 성공» 뿐이라, **누가 어떻게 올렸든 자동으로 반영된다** —
CodeBuild든, 로컬 `docker push`든, 콘솔 업로드든, 기존 digest에 태그를 다시
붙이는 것(= `PutImage`)이든.

**A. GitHub push 자동 (권장, 토큰 필요)**
`GitHubToken` 파라미터에 PAT(`repo` + `admin:repo_hook`)를 주고 배포하면
웹훅이 걸려서, 브랜치에 push할 때마다 빌드 → ECR → 재배포가 끝까지 자동으로 간다.
```bash
aws cloudformation deploy ... --parameter-overrides ... GitHubToken=ghp_xxx
```
> AWS는 GitHub 자격증명을 **계정+리전당 1개**만 저장한다. 이미 등록돼 있으면
> 이 파라미터를 비우고 배포해도 웹훅 없이 정상 동작한다(수동 빌드는 항상 가능).

**B. 수동 빌드 (토큰 없이)** — 스택 출력 `RebuildCommand`
```bash
aws codebuild start-build --region ap-northeast-2 --project-name midas-mcp-build
```
GitHub에 push해두고 이 한 줄만 실행하면 최신 코드로 다시 빌드·배포된다.
CodeBuild 콘솔의 **Start build** 버튼도 같은 동작.

**C. 직접 ECR에 push** — 자동 반영된다. 별도 조치 불필요.
단 두 가지만 지키면 된다.
- **`linux/arm64`여야 한다.** x86 이미지를 올리면 컨테이너가
  `exec format error`로 즉사한다. 로컬에서 올린다면
  `docker buildx build --platform linux/arm64 --push`.
- **감시 중인 태그(`ImageTag`, 기본 `latest`)여야 한다.** 다른 태그로 올리면
  아무 일도 일어나지 않는다(실험용으로 일부러 그렇게 해둔 것).

롤백은 옛 digest에 `latest`를 다시 붙이면 된다 — 이것도 push 이벤트를 만든다.
```bash
MANIFEST=$(aws ecr batch-get-image --repository-name midas-mcp \
  --image-ids imageDigest=sha256:<옛날digest> \
  --query 'images[0].imageManifest' --output text)
aws ecr put-image --repository-name midas-mcp --image-tag latest \
  --image-manifest "$MANIFEST"
```

**D. 재빌드 없이 재배포만** — 스택 출력 `ForceRedeploy`
ECR은 그대로 두고 현재 이미지를 다시 pull 해서 컨테이너만 갈아끼운다.
**이미지를 새로 올렸을 때 쓰는 명령이 아니다**(그건 위에서 자동으로 된다).
컨테이너가 죽었을 때, 정지 중이라 push 이벤트를 놓쳤을 때, 그냥 재시작하고
싶을 때 쓴다.

## 4. 검증

```bash
curl -s -X POST https://mcp.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-MIDAS-MAPI-Key: test-key" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```
200 + `serverInfo`가 나오면 성공.

## 5. 문제 생겼을 때

빌드 쪽 — 스택 출력 `BuildLogs` 링크(CodeBuild 콘솔) 또는:
```bash
aws codebuild list-builds-for-project --project-name midas-mcp-build --max-items 1
```

인스턴스 쪽 — 출력 `SsmConnect`로 셸을 열고:
```bash
docker ps                                   # midas / caddy 두 개가 보여야 정상
docker logs midas                           # 앱 로그
docker logs caddy                           # 인증서 발급 실패는 여기
systemctl status midas-redeploy             # 이미지 pull 상태
journalctl -u midas-redeploy -n 50          # pull 실패 이유
sudo cat /var/log/cloud-init-output.log     # 최초 부팅 셋업
docker inspect midas --format '{{.Image}}'  # 지금 돌고 있는 digest
```

`midas-redeploy`는 **실패하면 60초마다 재시도**한다. 첫 배포에서 EC2가
빌드보다 먼저 뜨는 게 정상이고, 빌드가 끝나면 이 재시도가 알아서 물어온다.
그래서 부팅 직후 `docker ps`에 `midas`가 없어도 몇 분 기다리면 된다.

## 6. 비용 / 정지 / 삭제

EventBridge Scheduler가 평일 08:00 start / 20:00 stop (Asia/Seoul).
`StartCron` / `StopCron` / `ScheduleTimezone` 파라미터로 변경.
정지 중에도 과금: EBS 20 GiB, Elastic IP, Route 53 호스팅존(약 $0.50/월).
CodeBuild는 빌드 시간당 과금이라 평소엔 0.

```bash
aws cloudformation delete-stack --region ap-northeast-2 --stack-name midas-mcp
```
ECR은 `EmptyOnDelete: true`라 이미지가 남아 있어도 함께 삭제된다.

---
### 주의
- **Dockerfile이 GitHub 리포에 있어야 한다.** CodeBuild는 리포를 클론해서
  빌드한다. `Dockerfile`, `.dockerignore`, `deploy/`를 커밋하지 않으면 빌드가
  실패한다(.gitignore에서 제외 해제 완료).
- **아키텍처.** CodeBuild가 ARM 네이티브로 빌드하므로 인스턴스는 `t4g.*`를
  유지해야 한다. x86(`t3.*`)으로 바꾸려면 `Environment.Type`을
  `LINUX_CONTAINER` + x86 이미지로 함께 바꿔야 한다.
- **서버 자체 인증이 없다.** Let's Encrypt HTTP-01 때문에 `AllowedCidr`가
  `0.0.0.0/0`이라, 도메인만 알면 누구나 MCP 핸드셰이크를 할 수 있다. MAPI 키는
  요청 헤더로 오므로 키가 새는 건 아니지만, 공개 전에 인증을 붙이는 게 맞다.
- **리포가 public이다.** CodeBuild가 자격증명 없이 클론할 수 있는 이유이기도
  하다. private으로 바꾸면 클론에도 `GitHubToken`이 필요해진다.
- **롤백**은 GitHub에서 되돌린 뒤 재빌드(B)가 가장 단순하다.
