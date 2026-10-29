"""Pipecat TTS service for Cloudflare Workers AI text-to-speech (Deepgram Aura models).

Calls the Workers AI REST API (`POST /accounts/{id}/ai/run/{model}`) and streams the raw PCM16
response at the pipeline's output sample rate. When the user interrupts, Pipecat cancels `run_tts`,
which closes the HTTP request.
"""

from collections.abc import AsyncGenerator

import aiohttp
from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService


class CloudflareTTSService(TTSService):
    Settings = TTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        aiohttp_session: aiohttp.ClientSession,
        model: str = "@cf/deepgram/aura-1",
        speaker: str = "asteria",
        **kwargs,
    ):
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            settings=self.Settings(model=model, voice=speaker, language=None),
            **kwargs,
        )
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._speaker = speaker
        self._session = aiohttp_session

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            async with self._session.post(
                self._url,
                json={
                    "text": text,
                    "speaker": self._speaker,
                    "encoding": "linear16",
                    "container": "none",
                    "sample_rate": self.sample_rate,
                },
                headers=self._headers,
            ) as response:
                # Errors come back as JSON; audio comes back as a binary body.
                if response.status != 200 or response.content_type == "application/json":
                    yield ErrorFrame(error=f"Workers AI TTS error {response.status}: {await response.text()}")
                    yield TTSStoppedFrame(context_id=context_id)
                    return

                await self.start_tts_usage_metrics(text)
                async for frame in self._stream_audio_frames_from_iterator(
                    response.content.iter_chunked(self.chunk_size), context_id=context_id
                ):
                    await self.stop_ttfb_metrics()
                    yield frame
        except Exception as e:
            yield ErrorFrame(error=f"Workers AI TTS request failed: {e}")
        finally:
            await self.stop_ttfb_metrics()
