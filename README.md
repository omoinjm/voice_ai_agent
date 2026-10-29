# Voice AI Agent

A voice agent on two channels, sharing one prompt, one set of tools, and one voice:

| Channel | Path |
|---|---|
| **Real-time calls** (browser and WhatsApp Business calls) | Pipecat bot on EC2 (us-east-1): Deepgram Flux STT (with end-of-turn detection) → Amazon Nova 2 Lite on Bedrock (Claude Haiku 4.5 once the account has access) → Deepgram Aura-2 streaming TTS |
| **WhatsApp voice notes** (testing via openwa) | n8n: openwa webhook → Cloudflare Whisper STT → Claude → VoxCPM2 → openwa `send-audio` (PTT) |

Every call is saved to Postgres as it happens (turns, tool calls, bookings), bookings show on a Metabase
dashboard, and callers get a WhatsApp message confirming any booking made, moved or cancelled on the call.

```
WhatsApp call (Cloud API) ─┐   signalling: Cloudflare tunnel; media: UDP to the Elastic IP
Browser (WebRTC) ──────────┴► EC2 us-east-1 ─ agent/ (Pipecat) ──► Deepgram Flux STT
                                              │  Nova 2 Lite (Bedrock) + tools ──► n8n /webhook/tool
                                              │    reschedule/cancel ──────────► Postgres (bookings)
                                              │  Deepgram Aura-2 TTS
                                              ├─ every turn ──────────────────► Postgres (calls, turns, tool calls)
                                              └─ on hang-up ─► WhatsApp booking confirmation (Cloud API)
                                                             ─► n8n /webhook/post-call (summary)
Metabase (bookings.njmtech.co.za) ◄── read-only ── Postgres

WhatsApp voice note ─► openwa-api ─► n8n /webhook/whatsapp-voice ─► cloudflare-stt ─► Claude
                    ◄── send-audio {ptt:true} ◄── VoxCPM2 /v1/audio/speech (ogg/opus)
```

## Stack

| Layer | Technology |
|---|---|
| Voice pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) 1.12 (Python 3.12), SmallWebRTC transport for both browser and WhatsApp calls |
| Speech to text | Deepgram Flux (`flux-general-en`), which also decides when the caller has finished speaking |
| LLM | Amazon Bedrock: Nova 2 Lite on the server, Claude Haiku 4.5 as the default once enabled; Workers AI GPT-OSS-120B and the Claude API as fallbacks (`agent/llm.py`) |
| Text to speech | Deepgram Aura-2 over a WebSocket; Workers AI Aura and VoxCPM2 on a Modal L4 GPU as alternatives |
| Telephony | WhatsApp Cloud API Calling (calls in) and messages (booking confirmations); openwa for voice-note testing |
| Business tools | n8n (tool webhooks, post-call summary, voice-note flow) |
| Data | PostgreSQL 17 (`asyncpg`): calls, turns, tool calls, bookings |
| Dashboard | Metabase, reading Postgres through a read-only role |
| Hosting | One EC2 c7i-flex.large (Ubuntu 24.04, pinned image) running Docker Compose; Elastic IP for call audio |
| Networking | Cloudflare Tunnel (`voice-agent.` and `bookings.njmtech.co.za`); no SSH or open TCP ports |
| Infrastructure as code | CloudFormation (`infra/ec2.yaml`, `infra/github-ci.yaml`); admin through SSM only |
| Secrets | `.env` locally, stored on AWS as an SSM SecureString; never in GitHub |
| CI/CD | GitHub Actions: ruff, shellcheck, cfn-lint, compose check, image build; deploys `main` over SSM through a GitHub OIDC role |
| Dependencies | uv (locked in `agent/uv.lock`), Docker images for everything |

## Layout

| Path | What |
|---|---|
| `agent/` | Pipecat bot (`bot.py`), LLM choice (`llm.py`: Bedrock, Workers AI or Claude API), tools (`tools.py`: n8n-backed tools plus booking reschedule/cancel), call records (`store.py`), WhatsApp booking messages (`notify.py`), TTS services (`cloudflare_tts.py`, `voxcpm_tts.py`), Workers AI LLM adapter (`cloudflare_llm.py`), test scripts (`_smoke_test.py`, `_call_test.py`) |
| `shared/system.md`, `shared/tools.json` | One persona and one tool list, used by both the bot and n8n |
| `db/` | Postgres schema (`10-schema.sql`, re-applied on every release) and dashboard roles (`20-roles.sh`) |
| `n8n-workflows/` | `whatsapp_voice_note`, `agent_tools` (the tool implementations; stubs for now), `post_call` |
| `tts/modal_app.py` | VoxCPM2 on a Modal L4 GPU (scale-to-zero). `/v1/audio/speech` (wav/ogg/pcm), `/v1/audio/stream` (chunked PCM16), `/health` |
| `infra/` | The EC2 server (`ec2.yaml`) and the GitHub Actions deploy role (`github-ci.yaml`), in CloudFormation |
| `scripts/` | `deploy-server.sh` (infra, env, release, run on the server), `metabase-setup.py` (dashboard setup) |
| `.github/workflows/ci-cd.yml` | Checks on every push and PR; deploys `main` to the server |

## Setup

### 1. Accounts and keys
`cp .env.example .env`, then fill it in:
- `ANTHROPIC_API_KEY`
- `DEEPGRAM_API_KEY` (the free credit is plenty for testing)
- `STT_API_KEY` and `STT_BASE_URL` of the existing `cloudflare-stt` Worker
- `OPENWA_API_KEY`
- `TOOLS_SECRET`: any random string

### 2. LLM
**Default: Claude on Amazon Bedrock** (`LLM_PROVIDER=bedrock`, model `BEDROCK_MODEL`, default `us.anthropic.claude-haiku-4-5-20251001-v1:0` in `AWS_REGION=us-east-1`). Locally the container reads your default `~/.aws` profile (mounted read-only); on EC2 it uses the instance role. Prompt caching is on, so later turns start faster.
**Fallback: Cloudflare Workers AI** (`LLM_PROVIDER=cloudflare`, model `CF_LLM_MODEL`, same account and token as the TTS, free daily allowance). `gpt-oss-120b` was the most accurate in simulated calls (uses tools, never invents bookings); it needs `max_tokens` around 1024 because its hidden reasoning counts against the limit. `cloudflare_llm.py` works around two Workers AI quirks: assistant tool-call messages need `content: ""` (not null), and number tokens can stream as JSON numbers.
**Optional: Claude** (`LLM_PROVIDER=anthropic`, needs Anthropic credits; set `ANTHROPIC_WORKSPACE_ID` only for `sk-ant-usr-` keys).
Simulated call with latency numbers (synthetic caller speech streamed in real time): `docker exec voice-agent python /app/_call_test.py "question one" "question two"`

### 2b. TTS
**Default: Cloudflare Workers AI** (Deepgram Aura, free daily allowance). Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_AI_TOKEN` (an API token with the Workers AI template) in `.env`; `CF_TTS_MODEL` and `CF_TTS_SPEAKER` choose the voice. Nothing to deploy.

**Optional: VoxCPM2 on Modal** (`TTS_PROVIDER=voxcpm`; Modal needs a payment method for GPUs):
```bash
docker compose run --rm modal token new                                   # opens a login link; saved in the modal_config volume
docker compose run --rm modal secret create voxcpm-auth VOXCPM_TOKEN=$(openssl rand -hex 24)   # put the same value in .env
docker compose run --rm modal deploy modal_app.py                         # prints the URL -> VOXCPM_URL
```
- **Voice:** by default the voice comes from the `VOICE_DESCRIPTION` prompt in `modal_app.py`. To clone a voice instead, add `tts/voices/agent.wav` (10–20 s, clean) and `agent.txt` (its exact transcript), then redeploy.
- **Test:** `curl -H "Authorization: Bearer $VOXCPM_TOKEN" -H 'content-type: application/json' -d '{"input":"Hello from Demo Co.","response_format":"wav"}' $VOXCPM_URL/v1/audio/speech -o hello.wav`
- **Cold start:** the first request after idle takes about 30–90 s while the container boots. After that, requests take seconds.

### 3. n8n
```bash
docker compose up -d n8n          # http://localhost:5679 (create the owner account, put it in .env)
for f in agent_tools post_call whatsapp_voice_note; do docker exec voice-n8n n8n import:workflow --input=/workflows/$f.json; done
for id in VoiceAgentTools0001 VoiceAgentPostCall1 VoiceAgentWaVoice1; do docker exec voice-n8n n8n publish:workflow --id=$id; done
docker restart voice-n8n
```
- The tools webhook is `POST /webhook/tool` with body `{tool, input, channel, phone}`.
- Replace the demo bodies in the **Run Tool** node with your real lookups and bookings.

### 4. Real-time bot locally (browser)
```bash
docker compose up -d --build agent                 # rebuild after changing agent/*.py
docker compose logs -f agent
```
- Open http://localhost:7860 and click Connect.
- The bot wakes the Modal container as soon as you connect.
- The `agent` service uses `network_mode: host`. WebRTC media goes over UDP on random ports, and Docker port mapping doesn't pass it through.

### 5. WhatsApp voice notes via openwa (test number)
Use a **separate WhatsApp number and session**. Never link a second client to the carwash number.
1. Join the gateway to this network: `docker network connect voice_ai_agent_default openwa-api`
2. Add `voice-n8n` to `SSRF_ALLOWED_HOSTS` in the gateway's `.env` (comma-separated), then restart the gateway.
3. In the openwa dashboard (http://127.0.0.1:2785):
   - Create session `voice-test` and scan the QR code with the test phone.
   - Add a webhook: URL `http://voice-n8n:5678/webhook/whatsapp-voice`, events `message.received`.
4. Optional: set `ALLOWED_PHONE_NUMBERS` so the bot only answers you.
5. Send the test number a voice note (or a text). It replies with a voice note.

### 6. WhatsApp Business calls (Meta Cloud API)
openwa can't take calls; they come through Meta's official Calling API. Pipecat answers them with the same bot.

**Prerequisites:**
- A Meta developer app.
- A Cloud API business number that isn't also on openwa or the WhatsApp app.
- Calling enabled in WhatsApp Manager.
- Business verification, for production.

**Steps:**
1. Add these to `.env`: `WHATSAPP_TOKEN` (a permanent system-user token), `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFICATION_TOKEN`.
2. Run `docker compose up -d --build agent cloudflared`. The `agent` service already starts the bot with `--whatsapp`. The named tunnel `voice-agent` serves the bot at **https://voice-agent.njmtech.co.za**. Set the Meta webhook to `https://voice-agent.njmtech.co.za/whatsapp`, subscribed to `calls`.
3. Meta's US sandbox test numbers can't call some countries (error 130497). Use a real business number.

### 7. Deploy to AWS (EC2, us-east-1)
The server runs the same compose stack (`agent`, `n8n`, `cloudflared`); only its `.env` differs (`LLM_PROVIDER=bedrock`, `PIPECAT_ICE_SERVERS` STUN so WebRTC peers get the Elastic IP). WhatsApp voice notes stay local, because the openwa gateway belongs to the carwash project.
```bash
scripts/deploy-server.sh infra       # c7i-flex.large + Elastic IP + role + deploy bucket (~$34/month while running)
scripts/deploy-server.sh app         # upload the repo + .env, build and start on the server (first build ~10 min)
scripts/deploy-server.sh n8n-setup   # first deploy only: n8n owner, tool + post-call workflows
scripts/deploy-server.sh run 'docker compose logs --tail 100 agent'
```
- Stop the local `cloudflared` (and `agent`) first: two connectors on the same tunnel split the traffic between them.
- No SSH or open TCP ports: HTTP comes through the tunnel, admin goes through SSM.
- To pause costs, stop the instance in the EC2 console (the Elastic IP still costs ~$3.60/month); `docker compose` services restart with it.
- The instance image is pinned in `infra/ec2.yaml` (`ImageId`), so `infra` updates don't replace the instance.
  Changing `ImageId` would, and it takes the Postgres and n8n data with it, so back up first.

### 8. Call records and dashboard
The bot saves calls, turns, tool calls and bookings to Postgres (`db`, schema in `db/10-schema.sql`) as they happen.
On the server, Metabase (`metabase`, compose profile `dashboard`) shows them at https://bookings.njmtech.co.za.
First time only: `scripts/deploy-server.sh run 'python3 scripts/metabase-setup.py'` creates the admin login
(`METABASE_ADMIN_PASSWORD`) and the Bookings dashboard.

### 9. CI/CD (GitHub Actions)
`.github/workflows/ci-cd.yml` runs ruff, shellcheck, cfn-lint, a compose check and an agent image build on every PR
and push. Pushes to `main` then run `scripts/deploy-server.sh release` and smoke-test both public URLs.
- AWS access is GitHub OIDC (no stored keys): `scripts/deploy-server.sh ci` creates the `voice-agent-github-deploy`
  role (`infra/github-ci.yaml`), whose ARN goes in the repo variable `AWS_DEPLOY_ROLE_ARN`.
- Secrets never go to GitHub. The server's `.env` lives in SSM; after changing `.env`, run
  `scripts/deploy-server.sh env` (applied on the next release).
- Every release re-applies `db/10-schema.sql`, so keep it idempotent (new columns also go in its
  `ADD COLUMN IF NOT EXISTS` section).
- A release restarts the bot, which drops any call in progress.

## Latency notes
- **Target:** about 1.5–2 s from the end of the caller's speech to the bot's first audio.
- **Where it shows up:** Pipecat logs a TTFB for each service (STT, LLM, TTS).
- **Main levers:**
  - Keep Modal warm during calls; `scaledown_window` is 5 min.
  - Lower `inference_timesteps` in `modal_app.py`.
  - Move TTS to Nano-vLLM-VoxCPM (about 2x faster on the same GPU) once you need concurrency.
