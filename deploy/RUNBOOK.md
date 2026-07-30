# 배포 런북 — 실행 방법

CloudShell에 yaml 하나 올리고 명령 하나면 빌드까지 굴러간다. 로컬에 설치할 것 없음.
**각 파라미터·리소스·옵션의 자세한 설명은 [`infra-ec2.md`](infra-ec2.md) 참고.**

## 1. 배포

CloudShell **Actions → Upload file**로 `deploy/infra-ec2.yaml` 업로드 후:

```bash
aws cloudformation deploy \
  --region ap-northeast-1 \
  --template-file infra-ec2.yaml \
  --stack-name midas-mcp \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      GitHubBranch=Deploy-Image \
      ServiceHostname=mcp.example.com \
      AcmeEmail=you@example.com
```

`ServiceHostname`·`AcmeEmail`은 필수(기본값 없음), `GitHubBranch`는 기본이 `master`라
다른 브랜치면 지정. 빌드는 3~5분, 끝나는 순간 EC2가 이미지를 받아 컨테이너를 띄운다.

출력값 확인:
```bash
aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name midas-mcp --query "Stacks[0].Outputs" --output table
```

## 2. 도메인 연결 (최초 1회)

스택 출력 `DnsRecordToCreate`에 찍힌 A 레코드를 쓰는 DNS 제공자에서 만든다:
```
mcp.example.com   A   <ElasticIP 출력값>
```
전파되면 Caddy가 Let's Encrypt 인증서를 자동 발급한다(계속 재시도).

### 반영이 바로 안 될 때 — DNS 캐싱(TTL)

**신규** 레코드는 대개 금방 뜨지만, **기존 레코드의 IP를 수정**하면 바로 반영 안 될 수 있다.
원인은 **TTL 캐싱**: 권위 서버는 새 값을 바로 내보내도, 이미 옛 값을 캐시한 리졸버
(ISP·사내 리졸버·OS·브라우저)가 **TTL이 만료될 때까지 옛 값을 계속 응답**한다. 수정 행위가
남의 캐시를 지워주지 않는다. (그래서 스택 재생성으로 **EIP가 바뀌면** A 레코드를 갱신해도
사내에서만 옛 IP로 한동안 물린다.)

대처:
```bash
ipconfig /flushdns                       # (Windows) 로컬 캐시 비우기
nslookup mcp.example.com 8.8.8.8         # 공용 리졸버로 권위 반영 확인(캐시 우회)
```
- **변경 예정이면 미리 TTL을 60~300초로 낮춰**두면 그만큼 빨리 퍼진다(변경 후 되돌려도 됨).
- 공용 리졸버(8.8.8.8/1.1.1.1)에 새 IP가 뜨면 권위 반영은 끝난 것. **사내 리졸버는 자체
  TTL로 더 오래 옛 값을 붙잡아** 사무실에서만 늦게 반영되는 경우가 흔하다.
- 삭제 후 재생성하면 "바로 되는" 것처럼 보이는 건 그 사이 **TTL이 경과**했거나 캐시에 항목이
  없어서지, 재생성이 강제 전파를 하는 건 아니다(신뢰할 방법 아님 — TTL 관리가 정답).
- 특정 IP가 실제로 서빙 중인지 DNS 무시하고 콕 찍어 확인:
  ```bash
  curl -sI --resolve mcp.example.com:443:<IP> https://mcp.example.com/healthz
  ```

> **EIP 고정 팁:** 스택을 재생성할 때마다 EIP가 새로 할당돼 DNS·방화벽을 다시 손봐야 한다.
> 자주 재배포한다면 EIP를 미리 할당해 고정(`EipAllocationId` 파라미터)해두면 재작업이 없다.

## 3. 이미지 갱신

**A. GitHub push 자동 (권장, 토큰 필요)** — `GitHubToken`에 PAT(`repo`+`admin:repo_hook`) 지정:
```bash
aws cloudformation deploy ... --parameter-overrides ... GitHubToken=ghp_xxx
```

**B. 수동 빌드 + 재배포 (토큰 없이)** — GitHub에 push 후. **ECR push 자동 재배포가
안 걸릴 수 있으므로**, 빌드 완료를 기다렸다가 인스턴스 재배포까지 함께 한다:
```bash
REGION=ap-northeast-1; PROJECT=midas-mcp-build; STACK=midas-mcp
INSTANCE=$(aws cloudformation describe-stacks --region $REGION --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='SsmConnect'].OutputValue" --output text | awk '{print $NF}')

# 1) 빌드 시작 → 완료(SUCCEEDED)까지 대기 (보통 3~5분)
BID=$(aws codebuild start-build --region $REGION --project-name $PROJECT --query 'build.id' --output text)
while :; do
  S=$(aws codebuild batch-get-builds --region $REGION --ids "$BID" --query 'builds[0].buildStatus' --output text)
  [ "$S" = SUCCEEDED ] && { echo "build ok"; break; }
  [ "$S" = IN_PROGRESS ] || { echo "build $S — 로그 확인(§5)"; break; }
  echo "  building..."; sleep 15
done

# 2) 새 이미지를 인스턴스에 pull & 재시작 (스택 출력 ForceRedeploy 와 동일)
aws ssm send-command --region $REGION --document-name AWS-RunShellScript \
  --instance-ids "$INSTANCE" --parameters commands=/opt/redeploy.sh
```
> `SsmConnect` 출력이 `aws ssm start-session --target i-xxxx` 형태라 `awk '{print $NF}'`로
> 인스턴스 id만 뽑는다. 자동 재배포(ECR push→EventBridge→SSM)가 정상이면 2)는 생략 가능.

**C. 직접 ECR push** — 자동 반영. `linux/arm64` + 감시 태그(`latest`)만 지킬 것:
```bash
docker buildx build --platform linux/arm64 --push -t <ECR>/midas-mcp:latest .
```

**D. 재빌드 없이 재배포만** — 스택 출력 `ForceRedeploy` (컨테이너만 재-pull).

롤백은 옛 digest에 `latest` 재부착:
```bash
MANIFEST=$(aws ecr batch-get-image --repository-name midas-mcp \
  --image-ids imageDigest=sha256:<옛날digest> \
  --query 'images[0].imageManifest' --output text)
aws ecr put-image --repository-name midas-mcp --image-tag latest --image-manifest "$MANIFEST"
```

## 4. 검증

OAuth가 켜진 채 배포되므로 `/mcp`는 토큰 없으면 `401`이 정상이다.

```bash
curl -s https://mcp.example.com/healthz                       # → ok  (인증 없는 생존 확인)
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://mcp.example.com/mcp \
  -H "Accept: application/json, text/event-stream" -d '{}'    # → 401  (정상)
```

**인스턴스 안에서 확인** — 컨테이너가 실제로 떠 있는지:

① 인스턴스에 접속 (스택 출력 `SsmConnect`, 이 명령을 실제로 실행):
```bash
aws ssm start-session --target i-xxxxxxxxxxxxxxxxx
```
② EC2 안에서 상태·로그 확인 (docker는 sudo 필요할 수 있음):
```bash
sudo docker ps                     # midas / caddy 둘 다 Up 이어야 정상
sudo docker logs midas --tail 20   # "Application startup complete" 나오면 정상
```

전체 OAuth 흐름(등록→로그인→토큰→도구 호출)까지 확인:
```bash
python -m midas_mcp.auth.check_flow https://mcp.example.com   # 19개 항목
```

실제 사용은 **Claude 커넥터에 `https://mcp.example.com/mcp` URL만** 넣고 연결 →
팝업 로그인 화면에서 MAPI 키를 붙여넣으면 끝.

## 5. 문제 생겼을 때

빌드 — 스택 출력 `BuildLogs`(콘솔) 또는:
```bash
aws codebuild list-builds-for-project --project-name midas-mcp-build --max-items 1
```

인스턴스 — 출력 `SsmConnect`로 셸을 열고:
```bash
docker ps                                   # midas / caddy 두 개면 정상
docker logs midas                           # 앱 로그
docker logs caddy                           # 인증서 발급 실패는 여기
systemctl status midas-redeploy             # 이미지 pull 상태
journalctl -u midas-redeploy -n 50          # pull 실패 이유
sudo cat /var/log/cloud-init-output.log     # 최초 부팅 셋업
```
`midas-redeploy`는 실패 시 60초마다 재시도한다. 부팅 직후 `midas`가 없어도 몇 분 기다리면 된다.

## 6. 정지 / 삭제

시작/정지는 매일 08:00 기동 / 24:00(자정) 정지 (Asia/Tokyo). 정지 중에도 EBS·Elastic IP는 과금.
```bash
aws cloudformation delete-stack --region ap-northeast-1 --stack-name midas-mcp
```
ECR은 `EmptyOnDelete: true`라 이미지째 삭제된다.
