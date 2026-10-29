"""VoxCPM2 text-to-speech service on a Modal GPU (scale-to-zero).

Endpoints (all require `Authorization: Bearer $VOXCPM_TOKEN`):

  GET  /health                 -> {"ok": true}. Also used to pre-warm a container before a call.
  POST /v1/audio/speech        -> OpenAI-compatible: {"input": "...", "response_format": "wav|ogg|pcm"}
                                  `ogg` is Opus mono 48k, ready for WhatsApp `send-audio` with ptt:true.
  POST /v1/audio/stream        -> {"input": "...", "sample_rate": 24000}; chunked raw PCM16 mono streamed
                                  as it is generated. Closing the request stops generation (barge-in).

Voice: if tts/voices/agent.wav + agent.txt exist, that clip is cloned ("ultimate cloning"). Otherwise
VOICE_DESCRIPTION is used as a VoxCPM2 voice-design prompt, so the service works without a clip.

Deploy:   modal secret create voxcpm-auth VOXCPM_TOKEN=<random>
          modal deploy tts/modal_app.py
"""

import asyncio
import io
import os
import subprocess
from math import gcd
from pathlib import Path

import modal

APP_NAME = "voxcpm-tts"
MODEL_ID = "openbmb/VoxCPM2"
VOICES_DIR = Path(__file__).parent / "voices"
REMOTE_VOICES = "/voices"
VOICE_DESCRIPTION = os.environ.get(
    "VOICE_DESCRIPTION",
    "A friendly, warm adult voice with a light South African English accent, natural conversational pace",
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("voxcpm", "fastapi[standard]", "soundfile", "scipy", "numpy")
    .env({"HF_HOME": "/cache/hf", "VOICE_DESCRIPTION": VOICE_DESCRIPTION})
    .add_local_dir(VOICES_DIR, REMOTE_VOICES)
)

hf_cache = modal.Volume.from_name("voxcpm-hf-cache", create_if_missing=True)
app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="L4",
    volumes={"/cache": hf_cache},
    secrets=[modal.Secret.from_name("voxcpm-auth")],
    # Scale to zero after 5 idle minutes; a call keeps the container warm.
    scaledown_window=300,
    timeout=600,
)
@modal.concurrent(max_inputs=8)
class TTS:
    @modal.enter()
    def load(self):
        from voxcpm import VoxCPM

        self.model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False)
        self.sample_rate = self.model.tts_model.sample_rate
        # The model is not safe for concurrent generation; requests queue on this lock.
        self.lock = asyncio.Lock()

        ref_wav = Path(REMOTE_VOICES) / "agent.wav"
        ref_txt = Path(REMOTE_VOICES) / "agent.txt"
        if ref_wav.exists() and ref_txt.exists():
            self.voice_kwargs = {
                "prompt_wav_path": str(ref_wav),
                "prompt_text": ref_txt.read_text().strip(),
                "reference_wav_path": str(ref_wav),
            }
            self.voice_prefix = ""
        else:
            self.voice_kwargs = {}
            self.voice_prefix = f"({os.environ['VOICE_DESCRIPTION']})"

        # First generation compiles kernels; do it here so the first real request is fast.
        self.model.generate(text=self.voice_prefix + "Ready.", **self.voice_kwargs)
        hf_cache.commit()

    def _gen_kwargs(self, text: str) -> dict:
        return {"text": self.voice_prefix + text, "cfg_value": 2.0, "inference_timesteps": 10, **self.voice_kwargs}

    def _resample_pcm16(self, wav, target_rate: int) -> bytes:
        import numpy as np
        from scipy.signal import resample_poly

        if target_rate != self.sample_rate:
            g = gcd(self.sample_rate, target_rate)
            wav = resample_poly(wav, target_rate // g, self.sample_rate // g)
        return (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    @modal.asgi_app()
    def web(self):
        import numpy as np
        import soundfile as sf
        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.responses import Response, StreamingResponse
        from pydantic import BaseModel

        token = os.environ["VOXCPM_TOKEN"]
        api = FastAPI(title="VoxCPM2 TTS")

        def require_auth(request: Request):
            if request.headers.get("authorization") != f"Bearer {token}":
                raise HTTPException(401, "invalid token")

        class SpeechRequest(BaseModel):
            input: str
            response_format: str = "wav"
            sample_rate: int | None = None  # only used for pcm
            model: str | None = None  # accepted for OpenAI compatibility, ignored
            voice: str | None = None  # ignored

        @api.get("/health", dependencies=[Depends(require_auth)])
        async def health():
            return {"ok": True, "sample_rate": self.sample_rate}

        @api.post("/v1/audio/speech", dependencies=[Depends(require_auth)])
        async def speech(req: SpeechRequest):
            if not req.input.strip():
                raise HTTPException(400, "input is empty")
            async with self.lock:
                wav = await asyncio.to_thread(self.model.generate, **self._gen_kwargs(req.input))

            if req.response_format == "pcm":
                pcm = self._resample_pcm16(wav, req.sample_rate or self.sample_rate)
                return Response(pcm, media_type="audio/pcm")

            buf = io.BytesIO()
            sf.write(buf, wav, self.sample_rate, format="WAV")
            if req.response_format == "wav":
                return Response(buf.getvalue(), media_type="audio/wav")
            if req.response_format == "ogg":
                ogg = subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "48000",
                     "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
                    input=buf.getvalue(), capture_output=True, check=True,
                ).stdout
                return Response(ogg, media_type="audio/ogg; codecs=opus")
            raise HTTPException(400, "response_format must be wav, ogg or pcm")

        class StreamRequest(BaseModel):
            input: str
            sample_rate: int = 24000

        @api.post("/v1/audio/stream", dependencies=[Depends(require_auth)])
        async def stream(req: StreamRequest):
            text = req.input.strip()
            if not text:
                raise HTTPException(400, "input is empty")
            loop = asyncio.get_running_loop()

            async def pcm_chunks():
                # generate_streaming is a blocking generator; run it in a thread and hand chunks
                # back through a queue. When the client disconnects (the bot was interrupted),
                # this generator is cancelled and `cancelled` stops generation early.
                queue: asyncio.Queue = asyncio.Queue()
                cancelled = False

                def produce():
                    try:
                        for chunk in self.model.generate_streaming(**self._gen_kwargs(text)):
                            if cancelled:
                                break
                            loop.call_soon_threadsafe(queue.put_nowait, chunk)
                    finally:
                        loop.call_soon_threadsafe(queue.put_nowait, None)

                async with self.lock:
                    task = loop.run_in_executor(None, produce)
                    try:
                        while (chunk := await queue.get()) is not None:
                            yield self._resample_pcm16(np.asarray(chunk, dtype=np.float32), req.sample_rate)
                    finally:
                        cancelled = True
                        await task

            return StreamingResponse(pcm_chunks(), media_type="audio/pcm")

        return api
