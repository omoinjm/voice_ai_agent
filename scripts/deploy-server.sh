#!/usr/bin/env bash
# Deploys the voice agent to one EC2 instance in us-east-1 (infra/ec2.yaml). The AWS CLI runs in Docker
# with ~/.aws mounted, and the server is reached through SSM only (no SSH).
#
#   scripts/deploy-server.sh infra           create/update the stack (EC2, Elastic IP, role, deploy bucket)
#   scripts/deploy-server.sh ci              create/update the GitHub Actions deploy role (infra/github-ci.yaml)
#   scripts/deploy-server.sh env             store the local .env (with server overrides) for the server
#   scripts/deploy-server.sh release         upload the repo, apply the db schema, `docker compose up -d --build`
#   scripts/deploy-server.sh app             env + release (a full deploy from this machine)
#   scripts/deploy-server.sh n8n-setup       first deploy only: create the n8n owner, import + publish workflows
#   scripts/deploy-server.sh run '<cmd>'     run a shell command in /opt/voice-agent on the server
#
# The server's .env is the local .env with server overrides (see server_env), stored as an SSM SecureString.
# CI (.github/workflows/ci-cd.yml) only runs `release`, so secrets never go to GitHub; run `env` after changing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=${STACK:-voice-agent}
export AWS_REGION=${AWS_REGION:-us-east-1}
WORK=.deploy  # git-ignored; mounted into the aws-cli container as /aws/.deploy

aws() {
  # Runs as you (not root) with ~/.aws writable, so `aws login` can refresh its cached token.
  docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/home/aws -v "$HOME/.aws:/home/aws/.aws" -v "$PWD:/aws" \
    -e AWS_REGION -e AWS_PROFILE -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
    amazon/aws-cli "$@"
}

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

# Runs a bash script on the instance via SSM Run Command, waits for it, and prints its output.
remote() {
  local id script cmd status
  id=$(output InstanceId)
  # Base64 keeps the script intact inside the JSON parameters.
  script=$(printf 'set -euo pipefail\nexport HOME=/root PATH=$PATH:/snap/bin AWS_DEFAULT_REGION=%s\ncd /opt/voice-agent\n%s\n' \
    "$AWS_REGION" "$1" | base64 -w0)
  cmd=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
    --parameters "{\"commands\":[\"echo $script | base64 -d > /tmp/voice-agent-cmd.sh && bash /tmp/voice-agent-cmd.sh\"],\"executionTimeout\":[\"3600\"]}" \
    --query Command.CommandId --output text)
  echo "SSM command $cmd on $id" >&2
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" --query Status --output text 2>/dev/null || echo Pending)
    case $status in Pending|InProgress|Delayed) sleep 10 ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" \
    --query '[StandardOutputContent, StandardErrorContent]' --output text
  echo "Status: $status" >&2
  [ "$status" = Success ]
}

# Local .env minus comments, with the settings that differ on the server.
server_env() {
  grep -v -E '^\s*(#|$)' .env | grep -v -E '^(LLM_PROVIDER|AWS_PROFILE|PIPECAT_ICE_SERVERS|BEDROCK_MODEL|COMPOSE_PROFILES)='
  echo "LLM_PROVIDER=bedrock"
  # The Metabase dashboard runs only on the server.
  echo "COMPOSE_PROFILES=dashboard"
  # The instance sits behind EC2's 1:1 NAT; STUN lets the bot advertise its Elastic IP to WebRTC peers.
  echo "PIPECAT_ICE_SERVERS=stun:stun.l.google.com:19302"
  # Nova 2 Lite until the account's Claude Haiku 4.5 agreement goes through (Haiku currently fails with
  # ResourceNotFoundException). Override per deploy: BEDROCK_MODEL=<model id> scripts/deploy-server.sh app
  echo "BEDROCK_MODEL=${BEDROCK_MODEL:-us.amazon.nova-2-lite-v1:0}"
}

case ${1:-} in
  infra)
    aws cloudformation deploy --template-file /aws/infra/ec2.yaml --stack-name "$STACK" --capabilities CAPABILITY_IAM
    aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs' --output table
    ;;

  ci)
    aws cloudformation deploy --template-file /aws/infra/github-ci.yaml --stack-name "$STACK-ci" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides "InstanceId=$(output InstanceId)" "DeployBucket=$(output DeployBucket)" "AppStackName=$STACK"
    aws cloudformation describe-stacks --stack-name "$STACK-ci" --query 'Stacks[0].Outputs' --output table
    ;;

  env)
    mkdir -p "$WORK"
    trap 'rm -f "$WORK/server.env"' EXIT
    server_env > "$WORK/server.env"
    size=$(wc -c < "$WORK/server.env")
    [ "$size" -lt 4096 ] || { echo "server .env is $size bytes; SSM standard parameters hold 4096" >&2; exit 1; }
    aws ssm put-parameter --name "$(output EnvParameterName)" --type SecureString --overwrite \
      --value file:///aws/.deploy/server.env >/dev/null
    echo "Stored the server .env; it's applied on the next release."
    ;;

  release)
    mkdir -p "$WORK"
    tar czf "$WORK/bundle.tgz" --exclude=./.env --exclude=./.deploy --exclude=./.claude --exclude=./.git \
      --exclude=./.github --exclude=.venv --exclude=__pycache__ --exclude='*.wav' .
    bucket=$(output DeployBucket)
    param=$(output EnvParameterName)
    aws s3 cp /aws/.deploy/bundle.tgz "s3://$bucket/bundle.tgz"
    remote "
aws s3 cp s3://$bucket/bundle.tgz /tmp/bundle.tgz
tar xzf /tmp/bundle.tgz
umask 077
# Piped through cat: the snap aws-cli writes nothing (and still exits 0) when stdout is redirected to a file.
aws ssm get-parameter --name $param --with-decryption --query Parameter.Value --output text | cat > .env.new
# An empty .env would start every service with blank keys, so refuse it.
[ -s .env.new ] || { echo 'fetched .env is empty' >&2; exit 1; }
mv .env.new .env
# Schema first (db/ is idempotent), so the new bot never runs against missing columns.
docker compose up -d --wait db
docker compose exec -T -e PGOPTIONS='-c client_min_messages=warning' db \
  psql -U voice -d voice -v ON_ERROR_STOP=1 -q -f /docker-entrypoint-initdb.d/10-schema.sql
docker compose exec -T db sh /docker-entrypoint-initdb.d/20-roles.sh
docker compose up -d --build
docker compose ps"
    echo "Deployed. Public IP: $(output PublicIp)"
    ;;

  app)
    "$0" env
    "$0" release
    ;;

  n8n-setup)
    remote '
env_val() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
until curl -sf localhost:5679/healthz >/dev/null; do sleep 3; done
curl -s -X POST localhost:5679/rest/owner/setup -H "content-type: application/json" \
  -d "{\"email\":\"$(env_val N8N_OWNER_EMAIL)\",\"firstName\":\"Voice\",\"lastName\":\"Agent\",\"password\":\"$(env_val N8N_OWNER_PASSWORD)\"}" \
  | head -c 300; echo
for f in agent_tools post_call whatsapp_voice_note; do docker exec voice-n8n n8n import:workflow --input=/workflows/$f.json; done
for id in VoiceAgentTools0001 VoiceAgentPostCall1; do docker exec voice-n8n n8n publish:workflow --id=$id; done
docker restart voice-n8n'
    ;;

  run)
    remote "${2:?usage: deploy-server.sh run '<command>'}"
    ;;

  *)
    sed -n '2,14p' "$0"
    exit 1
    ;;
esac
