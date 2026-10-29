"""Simulated call: a synthetic caller's speech is streamed in real time through the same STT -> LLM -> TTS
setup as bot.py, and the delay from the end of the caller's speech to the bot's first audio is measured.
Not used by the bot. Run: docker exec voice-agent python /app/_call_test.py "question one" "question two"
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp
from pipecat.frames.frames import (FunctionCallInProgressFrame, InputAudioRawFrame, LLMTextFrame,
                                   TranscriptionFrame, TTSAudioRawFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.workers.runner import WorkerRunner

from llm import make_llm
from speech_text import SpeechTextFilter
from tools import load_tools, register_n8n_tools, tools_schema

SHARED = Path(os.environ.get("SHARED_DIR", "/shared"))
RATE = 16000


class Probe(FrameProcessor):
    """Records what passes through one point of the pipeline."""

    def __init__(self, log):
        super().__init__()
        self.log = log

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self.log.append(("heard", time.time(), frame.text))
        elif isinstance(frame, FunctionCallInProgressFrame):
            self.log.append(("tool", time.time(), frame.function_name))
        elif isinstance(frame, LLMTextFrame):
            self.log.append(("text", time.time(), frame.text))
        elif isinstance(frame, TTSAudioRawFrame):
            self.log.append(("audio", time.time(), len(frame.audio)))
        await self.push_frame(frame, direction)


async def caller_speech(session, text):
    async with session.post(
        "https://api.deepgram.com/v1/speak",
        params={"model": "aura-2-apollo-en", "encoding": "linear16", "sample_rate": RATE, "container": "none"},
        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
        json={"text": text},
    ) as r:
        r.raise_for_status()
        return await r.read()


async def main(questions):
    log = []
    async with aiohttp.ClientSession() as session:
        stt = DeepgramFluxSTTService(api_key=os.environ["DEEPGRAM_API_KEY"],
                                     enable_eager_end_of_turn=os.getenv("FLUX_EAGER_EOT", "false") == "true",
                                     settings=DeepgramFluxSTTService.Settings(model="flux-general-en"))
        llm = make_llm((SHARED / "system.md").read_text())
        tools = load_tools(SHARED)
        register_n8n_tools(llm, tools, session, {"channel": "web"})
        tts = DeepgramTTSService(api_key=os.environ["DEEPGRAM_API_KEY"],
                                 settings=DeepgramTTSService.Settings(voice=os.environ["DEEPGRAM_TTS_VOICE"]),
                                 text_filters=[SpeechTextFilter()])

        context = LLMContext(tools=tools_schema(tools))
        user_agg, assistant_agg = LLMContextAggregatorPair(
            context, user_params=LLMUserAggregatorParams(
                user_turn_strategies=stt.recommended_user_turn_strategies(enable_interruptions=True)))
        worker = PipelineWorker(
            Pipeline([stt, Probe(log), user_agg, llm, Probe(log), tts, Probe(log), assistant_agg]),
            params=PipelineParams(audio_in_sample_rate=RATE, audio_out_sample_rate=24000))
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        run = asyncio.create_task(runner.run())
        await asyncio.sleep(1.5)

        silence = b"\0" * (RATE * 2 // 50)  # 20 ms
        for q in questions:
            pcm = await caller_speech(session, q)
            start = len(log)
            for i in range(0, len(pcm), len(silence)):  # stream at real-time pace, like a call
                await worker.queue_frame(InputAudioRawFrame(audio=pcm[i:i + len(silence)].ljust(len(silence), b"\0"),
                                                            sample_rate=RATE, num_channels=1))
                await asyncio.sleep(0.02)
            spoke_until = time.time()
            for _ in range(600):  # keep sending silence for up to 12 s while the bot answers
                await worker.queue_frame(InputAudioRawFrame(audio=silence, sample_rate=RATE, num_channels=1))
                await asyncio.sleep(0.02)
            ev = log[start:]
            heard = " ".join(t for k, _, t in ev if k == "heard")
            tools_used = [t for k, _, t in ev if k == "tool"]
            reply = "".join(t for k, _, t in ev if k == "text").strip()
            first = next((ts for k, ts, _ in ev if k == "audio"), None)
            heard_at = next((ts for k, ts, _ in ev if k == "heard"), None)
            print(f"\nCALLER: {q}\n  heard: {heard!r}\n  tools: {tools_used}\n  reply: {reply[:200]!r}")
            print(f"  end of speech -> transcript: {heard_at and round(heard_at - spoke_until, 2)}s | "
                  f"end of speech -> first bot audio: {first and round(first - spoke_until, 2)}s")
        await runner.cancel()
        await asyncio.wait_for(run, 10)


asyncio.run(main(sys.argv[1:] or ["Hi there, what are your opening hours on Saturday?"]))
