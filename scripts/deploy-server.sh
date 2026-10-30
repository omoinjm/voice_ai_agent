#!/usr/bin/env bash
# Deploys the voice agent to one EC2 instance in us-east-1 (infra/ec2.yaml). The AWS CLI runs in Docker
# with ~/.aws mounted, and the server is reached through SSM only (no SSH).
#
#   scripts/deploy-server.sh up              set up everything from scratch: infra, ci, env, release, n8n, dashboard
#   scripts/deploy-server.sh down            back up the database, then delete everything this project created in AWS
#   scripts/deploy-server.sh backup          pg_dump the server's database to .deploy/backups/
#   scripts/deploy-server.sh restore [file]  replace the server's database with a backup (default: the newest)
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

# output <key> [stack]: an output of the app stack (or of the given stack).
output() {
  aws cloudformation describe-stacks --stack-name "${2:-$STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

# Runs a bash script on the instance via SSM Run Command, waits for it, and prints its output.
remote() {
  local id script cmd status
  id=$(output InstanceId)
  # Base64 keeps the script intact inside the JSON parameters.
  script=$(printf 'set -euo pipefail\nexport HOME=/root PATH=$PATH:/snap/bin AWS_DEFAULT_REGION=%s\nmkdir -p /opt/voice-agent\ncd /opt/voice-agent\n%s\n' \
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
  up)
    [ -f .env ] || { echo "Fill in .env first (cp .env.example .env)." >&2; exit 1; }
    "$0" infra
    "$0" ci
    role=$(output DeployRoleArn "$STACK-ci")
    if command -v gh >/dev/null; then
      gh variable set AWS_DEPLOY_ROLE_ARN --body "$role"
    else
      echo "Set the GitHub repo variable AWS_DEPLOY_ROLE_ARN to $role (gh isn't installed)." >&2
    fi
    # A new instance takes a few minutes to register with SSM and to install Docker (user data).
    id=$(output InstanceId)
    echo "Waiting for $id to come online in SSM..." >&2
    until [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
      --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ]; do sleep 10; done
    # Exit code 2 means user data finished with warnings, which is fine.
    remote 'cloud-init status --wait >/dev/null || [ $? -eq 2 ]'
    "$0" app
    "$0" n8n-setup
    "$0" run 'python3 scripts/metabase-setup.py'
    echo "Set up. Point the Meta webhook at https://voice-agent.njmtech.co.za/whatsapp if it isn't already."
    ;;

  down)
    # Both stacks (instance, its disk, Elastic IP, roles, security group, deploy bucket) and the .env parameter.
    # Left alone: the account's GitHub OIDC provider (shared with other repos) and Bedrock model access.
    echo "This deletes the voice agent server in $AWS_REGION and all data on it (Postgres call records, n8n, Metabase)."
    read -rp "Type '$STACK' to confirm: " answer
    [ "$answer" = "$STACK" ] || { echo "Aborted." >&2; exit 1; }
    # The data goes with the instance, so keep a copy first. SKIP_BACKUP=1 when the server is stopped or broken.
    if [ -z "${SKIP_BACKUP:-}" ] && aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
      "$0" backup || { echo "Backup failed, nothing deleted. Rerun with SKIP_BACKUP=1 to delete anyway." >&2; exit 1; }
    fi
    if aws cloudformation describe-stacks --stack-name "$STACK-ci" >/dev/null 2>&1; then
      aws cloudformation delete-stack --stack-name "$STACK-ci"
      aws cloudformation wait stack-delete-complete --stack-name "$STACK-ci"
      echo "Deleted stack $STACK-ci"
    fi
    param=/voice-agent/env
    if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
      param=$(output EnvParameterName)
      # CloudFormation can't delete a bucket that still has objects in it.
      aws s3 rm "s3://$(output DeployBucket)" --recursive
      aws cloudformation delete-stack --stack-name "$STACK"
      aws cloudformation wait stack-delete-complete --stack-name "$STACK"
      echo "Deleted stack $STACK"
    fi
    # Written by `env`, not by the stack, so it outlives it.
    if aws ssm get-parameter --name "$param" >/dev/null 2>&1; then
      aws ssm delete-parameter --name "$param"
      echo "Deleted parameter $param"
    fi
    # Without the variable, pushes to main skip the deploy job instead of failing it.
    if command -v gh >/dev/null; then gh variable delete AWS_DEPLOY_ROLE_ARN 2>/dev/null || true; fi
    echo "Done. The Cloudflare tunnel and DNS records are untouched (they aren't AWS)."
    ;;

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

  backup)
    # Dumped on the server, passed through the deploy bucket (the instance can't reach this machine) and
    # downloaded. The bucket copy expires with the bucket's lifecycle rule; the local one is the backup.
    mkdir -p "$WORK/backups"
    name=voice-db-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
    bucket=$(output DeployBucket)
    remote "
docker compose exec -T db pg_dump -U voice -d voice --clean --if-exists | gzip > /tmp/$name
aws s3 cp /tmp/$name s3://$bucket/backups/$name --only-show-errors
rm -f /tmp/$name"
    aws s3 cp "s3://$bucket/backups/$name" "/aws/$WORK/backups/$name" --only-show-errors
    # A dump of a reachable database is never empty, even with no rows.
    gzip -t "$WORK/backups/$name" && [ "$(gzip -dc "$WORK/backups/$name" | wc -c)" -gt 0 ] \
      || { echo "Backup $WORK/backups/$name is empty or corrupt" >&2; exit 1; }
    echo "Backed up the database to $WORK/backups/$name"
    ;;

  restore)
    # The voice database only; Metabase keeps its own (metabase-setup.py recreates the dashboard).
    file=${2:-$(ls -t "$WORK"/backups/voice-db-*.sql.gz 2>/dev/null | head -1)}
    [ -f "$file" ] && gzip -t "$file" || { echo "usage: deploy-server.sh restore [<backup>.sql.gz]" >&2; exit 1; }
    echo "This replaces the server's call records and bookings with $file."
    read -rp "Type '$STACK' to confirm: " answer
    [ "$answer" = "$STACK" ] || { echo "Aborted." >&2; exit 1; }
    name=restore-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
    bucket=$(output DeployBucket)
    # Copied under the repo first: the aws-cli container only sees this directory.
    mkdir -p "$WORK"
    trap 'rm -f "$WORK/$name"' EXIT
    cp "$file" "$WORK/$name"
    aws s3 cp "/aws/$WORK/$name" "s3://$bucket/backups/$name" --only-show-errors
    remote "
aws s3 cp s3://$bucket/backups/$name /tmp/$name --only-show-errors
# The bot writes every turn; stop it so nothing lands mid-restore, and start it again however this ends.
docker compose stop agent
trap 'rm -f /tmp/$name; docker compose start agent' EXIT
# One transaction: a failed restore leaves the database as it was.
gzip -dc /tmp/$name | docker compose exec -T -e PGOPTIONS='-c client_min_messages=warning' db \
  psql -U voice -d voice -v ON_ERROR_STOP=1 -q -1
# An older dump brings back an older schema; re-apply the current one and the dashboard role's grants.
docker compose exec -T -e PGOPTIONS='-c client_min_messages=warning' db \
  psql -U voice -d voice -v ON_ERROR_STOP=1 -q -f /docker-entrypoint-initdb.d/10-schema.sql
docker compose exec -T db sh /docker-entrypoint-initdb.d/20-roles.sh"
    echo "Restored $file"
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
    sed -n '2,18p' "$0"
    exit 1
    ;;
esac
