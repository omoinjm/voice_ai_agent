"""Real-time voice agent: Deepgram STT -> LLM (tools via n8n) -> TTS.

The LLM is Claude on Amazon Bedrock by default; LLM_PROVIDER=cloudflare (Workers AI GPT-OSS-120B) or
anthropic (Claude API) are fallbacks (see llm.py).

STT is Deepgram Flux (it also decides when the caller has finished). TTS is Deepgram Aura-2 streamed over a
WebSocket by default; TTS_PROVIDER=cloudflare uses Workers AI Aura, TTS_PROVIDER=voxcpm uses VoxCPM2 on Modal.

Runs in Docker (`docker compose up -d --build agent`): browser test client at http://localhost:7860,
and Meta WhatsApp Business calls at /whatsapp (needs the WHATSAPP_* env vars and the public tunnel).
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from llm import make_llm
from notify import booking_message, send_whatsapp_text
from notify import spoken_time
from tools import BOOKING_TOOLS, load_tools, register_booking_tools, register_n8n_tools, run_n8n_tool, tools_schema
from cloudflare_tts import CloudflareTTSService
from speech_text import SpeechTextFilter
from store import CallStore
from voxcpm_tts import VoxCPMTTSService

load_dotenv(override=True)

# /shared in Docker; the repo's shared/ folder when run from agent/ locally.
SHARED_DIR = Path(os.environ.get("SHARED_DIR", Path(__file__).resolve().parent.parent / "shared"))


def caller_info(runner_args: RunnerArguments) -> dict:
    """WhatsApp calls carry the caller's number in runner_args.body (a WhatsAppConnectCall)."""
    phone = getattr(runner_args.body, "from_", None)
    return {"channel": "whatsapp_call", "phone": phone} if phone else {"channel": "web"}


async def post_call(session: aiohttp.ClientSession, call: dict):
    url = f"{os.environ.get('N8N_URL', 'http://localhost:5679').rstrip('/')}/webhook/post-call"
    try:
        async with session.post(
            url, json=call, headers={"X-Tools-Secret": os.environ.get("TOOLS_SECRET", "")},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            logger.info(f"Post-call webhook: {response.status}")
    except Exception as e:
        logger.warning(f"Post-call webhook failed: {e}")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    caller = caller_info(runner_args)
    call_id = runner_args.session_id or str(uuid.uuid4())
    started_at = datetime.now(UTC)
    logger.info(f"Starting bot for {caller}, call {call_id}")

    store = CallStore()
    await store.connect()
    await store.start_call(call_id, caller["channel"], caller.get("phone"), started_at)
    # A number's first call of the day hears the services and prices; later calls go straight to booking.
    returning = bool(caller.get("phone")) and await store.earlier_calls_today(caller["phone"], call_id) > 0
    # Upcoming bookings take priority: the caller is asked whether to reschedule or cancel them.
    active = await store.active_bookings(caller["phone"]) if caller.get("phone") else []
    logger.info(f"{'Returning' if returning else 'First'} caller today, {len(active)} active booking(s)")

    async with aiohttp.ClientSession() as session:
        # Fetched while the call connects, so the greeting can include them without a tool round trip.
        business_info = None if returning or active else asyncio.create_task(
            run_n8n_tool(session, "get_business_info", {"topic": "all"}, caller)
        )
        tts_provider = os.getenv("TTS_PROVIDER", "deepgram")
        if tts_provider == "deepgram":
            # Aura-2 streamed over a WebSocket: audio starts ~0.2 s after the first sentence arrives.
            tts = DeepgramTTSService(
                api_key=os.environ["DEEPGRAM_API_KEY"],
                settings=DeepgramTTSService.Settings(voice=os.getenv("DEEPGRAM_TTS_VOICE", "aura-2-asteria-en")),
                text_filters=[SpeechTextFilter()],
            )
            warmup = asyncio.create_task(asyncio.sleep(0))
        elif tts_provider == "voxcpm":
            tts = VoxCPMTTSService(
                base_url=os.environ["VOXCPM_URL"], token=os.environ["VOXCPM_TOKEN"], aiohttp_session=session
            )
            # Start waking the GPU container now; it overlaps with WebRTC/STT setup.
            warmup = asyncio.create_task(tts.warmup())
        else:
            tts = CloudflareTTSService(
                account_id=os.environ["CLOUDFLARE_ACCOUNT_ID"],
                api_token=os.environ["CLOUDFLARE_AI_TOKEN"],
                aiohttp_session=session,
                model=os.getenv("CF_TTS_MODEL", "@cf/deepgram/aura-1"),
                speaker=os.getenv("CF_TTS_SPEAKER", "asteria"),
            )
            # Nothing to wake up: Workers AI is always on.
            warmup = asyncio.create_task(asyncio.sleep(0))

        # Flux detects end of turn itself (built for voice agents), so the aggregator follows its turn
        # events instead of VAD + the local turn analyzer, which cut callers off mid-sentence.
        use_flux = os.getenv("STT_PROVIDER", "flux") == "flux"
        if use_flux:
            # Eager end of turn: Flux predicts the end of the turn a few hundred ms before committing,
            # and the reply starts generating then (discarded if the caller keeps talking).
            stt = DeepgramFluxSTTService(
                api_key=os.environ["DEEPGRAM_API_KEY"],
                enable_eager_end_of_turn=os.getenv("FLUX_EAGER_EOT", "false") == "true",
                settings=DeepgramFluxSTTService.Settings(model="flux-general-en"),
            )
            user_params = LLMUserAggregatorParams(
                user_turn_strategies=stt.recommended_user_turn_strategies(enable_interruptions=True)
            )
        else:
            stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
            user_params = LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())

        system = (SHARED_DIR / "system.md").read_text()
        # Without today's date the model guesses one (it booked "Friday" in June 2024).
        now = datetime.now(ZoneInfo("Africa/Johannesburg"))
        system += f"\nIt is now {now:%A %-d %B %Y, %H:%M} in South Africa. Work out dates such as \"Friday\" from this."
        if caller.get("phone"):
            system += f"\nThis is a WhatsApp call. The caller's WhatsApp number is {caller['phone']}."
        if active:
            listed = "; ".join(
                f"{b['reference']}: {b['service']} on {spoken_time(b['requested'])}, for {b['name']}" for b in active
            )
            system += (f"\nThe caller has these active bookings: {listed}. Use reschedule_booking or cancel_booking "
                       "to change them.")
        llm = make_llm(system)
        tools = load_tools(SHARED_DIR)
        register_n8n_tools(llm, tools, session, caller, store, call_id)
        register_booking_tools(llm, caller, store, call_id)

        context = LLMContext(tools=tools_schema(tools + BOOKING_TOOLS))
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=user_params,
        )

        # Each finished turn is logged and saved as it happens, so a dropped call keeps everything said so far.
        @user_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(aggregator, strategy, message):
            if message.content:
                logger.info(f"Caller: {message.content}")
                await store.add_turn(call_id, "caller", message.content)

        @assistant_aggregator.event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(aggregator, message):
            if message.content:
                logger.info(f"Agent{' (interrupted)' if message.interrupted else ''}: {message.content}")
                await store.add_turn(call_id, "agent", message.content, message.interrupted)

        pipeline = Pipeline([
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ])

        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            # RTVI messages feed the browser client over the WebRTC data channel. WhatsApp calls have no
            # data channel, so they'd only fill the queue and log "Data channel not ready" warnings.
            enable_rtvi=caller["channel"] == "web",
        )
        runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
        await runner.add_workers(worker)

        async def greet():
            await warmup
            if active:
                instruction = (
                    f"The caller has {'an active booking' if len(active) == 1 else 'active bookings'} (see above). "
                    f"Greet them by name ({active[0]['name']}), remind them briefly of the booking's service and "
                    "time, and ask whether they'd like to reschedule or cancel it, or whether there's something else."
                )
            elif returning:
                instruction = (
                    "This caller has already called today. Greet them briefly, welcome them back, "
                    "and ask which booking they'd like to make."
                )
            else:
                info = await business_info
                if "error" in info:
                    instruction = "Greet the caller briefly and ask how you can help."
                else:
                    instruction = (
                        "This is the caller's first call today. Greet them briefly, tell them the services we offer "
                        "and their prices, then ask how you can help. Business info (answer later questions from "
                        f"this too): {json.dumps(info)}"
                    )
            context.add_message({"role": "developer", "content": instruction})
            await worker.queue_frames([LLMRunFrame()])

        if caller["channel"] == "web":
            # The browser client signals when it's ready to play audio.
            @worker.rtvi.event_handler("on_client_ready")
            async def on_client_ready(rtvi):
                await greet()

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Client connected")
            if caller["channel"] != "web":
                await greet()

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected")
            await runner.cancel()

        try:
            await runner.run()
        finally:
            ended_at = datetime.now(UTC)
            await store.end_call(call_id, ended_at)
            # Bookings made on a WhatsApp call get a confirmation message to the caller's WhatsApp.
            if caller.get("phone"):
                bookings = await store.unconfirmed_bookings(call_id, caller["phone"])
                if bookings and await send_whatsapp_text(session, caller["phone"], booking_message(bookings)):
                    await store.mark_confirmed([b["reference"] for b in bookings])
            await store.close()
            transcript = [
                {"role": m["role"], "content": m["content"]}
                for m in context.get_messages()
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
            ]
            await post_call(session, {
                "callId": call_id,
                **caller,
                "startedAt": started_at.isoformat(),
                "endedAt": ended_at.isoformat(),
                "transcript": transcript,
            })


async def bot(runner_args: RunnerArguments):
    transport_params = {
        # Used for both the browser client and WhatsApp calls (both are SmallWebRTC).
        "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
