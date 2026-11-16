"""Headless smoke test: real system prompt + n8n tools -> Workers AI LLM -> Workers AI TTS. Not used by the bot."""
import asyncio, os, time
from pathlib import Path
import aiohttp
from pipecat.frames.frames import (EndFrame, ErrorFrame, FunctionCallInProgressFrame, FunctionCallResultFrame,
                                   LLMContextFrame, LLMTextFrame, TTSAudioRawFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from cloudflare_llm import CloudflareLLMService
from pipecat.workers.runner import WorkerRunner
from cloudflare_tts import CloudflareTTSService
from tools import load_tools, register_n8n_tools, tools_schema

SHARED = Path(os.environ.get("SHARED_DIR", "/shared"))

class Collect(FrameProcessor):
    def __init__(self):
        super().__init__(); self.t0 = time.time(); self.text = ""; self.audio = 0; self.first_audio = None; self.events = []
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame): self.text += frame.text
        elif isinstance(frame, TTSAudioRawFrame):
            self.first_audio = self.first_audio or time.time() - self.t0; self.audio += len(frame.audio)
        elif isinstance(frame, FunctionCallInProgressFrame): self.events.append(f"tool call {frame.function_name}({frame.arguments})")
        elif isinstance(frame, FunctionCallResultFrame): self.events.append(f"tool result {str(frame.result)[:120]}")
        elif isinstance(frame, ErrorFrame): self.events.append(f"ERROR {frame.error}")
        await self.push_frame(frame, direction)

async def turn(question):
    async with aiohttp.ClientSession() as session:
        llm = CloudflareLLMService(
            account_id=os.environ["CLOUDFLARE_ACCOUNT_ID"], api_token=os.environ["CLOUDFLARE_AI_TOKEN"],
            settings=CloudflareLLMService.Settings(model=os.environ["CF_LLM_MODEL"],
                                               system_instruction=(SHARED / "system.md").read_text(), max_tokens=300))
        tools = load_tools(SHARED)
        register_n8n_tools(llm, tools, session, {"channel": "web"})
        tts = CloudflareTTSService(account_id=os.environ["CLOUDFLARE_ACCOUNT_ID"], api_token=os.environ["CLOUDFLARE_AI_TOKEN"],
                                   aiohttp_session=session, model=os.environ["CF_TTS_MODEL"], speaker=os.environ["CF_TTS_SPEAKER"])
        context = LLMContext(tools=tools_schema(tools))
        context.add_message({"role": "user", "content": question})
        pre, out = Collect(), Collect()
        _, assistant_aggregator = LLMContextAggregatorPair(context)
        worker = PipelineWorker(Pipeline([llm, pre, tts, out, assistant_aggregator]), params=PipelineParams(audio_out_sample_rate=24000))
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        run = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        await worker.queue_frames([LLMContextFrame(context)])
        await asyncio.sleep(12)
        await runner.cancel()
        await asyncio.wait_for(run, 10)
        print(f"\nQ: {question}\n  events: {pre.events}\n  reply: {pre.text.strip()[:300]!r}\n  first audio {out.first_audio and round(out.first_audio,2)}s, {out.audio/2/24000:.1f}s of speech")

async def main():
    for q in ["Hi, what kind of work does your business do?", "Can I book an appointment for tomorrow at 10am?"]:
        try: await turn(q)
        except Exception as e: print("FAILED", q, repr(e))

asyncio.run(main())
