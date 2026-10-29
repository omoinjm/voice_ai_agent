"""Pipecat TTS service for the VoxCPM2 Modal app (tts/modal_app.py).

Streams raw PCM16 from `POST /v1/audio/stream` at the pipeline's output sample rate. When the
user interrupts, Pipecat cancels `run_tts`, which closes the HTTP request; the server sees the
disconnect and stops generating.
"""

from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService


class VoxCPMTTSService(TTSService):
    Settings = TTSSettings
    _settings: Settings

    def __init__(self, *, base_url: str, token: str, aiohttp_session: aiohttp.ClientSession, **kwargs):
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            settings=self.Settings(model="openbmb/VoxCPM2", voice=None, language=None),
            **kwargs,
        )
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._session = aiohttp_session

    def can_generate_metrics(self) -> bool:
        return True

    async def warmup(self):
        """Wake the Modal container (scale-to-zero) so the first reply doesn't pay the cold start."""
        try:
            async with self._session.get(
                f"{self._base_url}/health", headers=self._headers, timeout=aiohttp.ClientTimeout(total=180)
            ) as response:
                logger.info(f"VoxCPM warmup: {response.status}")
        except Exception as e:
            logger.warning(f"VoxCPM warmup failed: {e}")

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            async with self._session.post(
                f"{self._base_url}/v1/audio/stream",
                json={"input": text, "sample_rate": self.sample_rate},
                headers=self._headers,
            ) as response:
                if response.status != 200:
                    yield ErrorFrame(error=f"VoxCPM error {response.status}: {await response.text()}")
                    yield TTSStoppedFrame(context_id=context_id)
                    return

                await self.start_tts_usage_metrics(text)
                async for frame in self._stream_audio_frames_from_iterator(
                    response.content.iter_chunked(self.chunk_size), context_id=context_id
                ):
                    await self.stop_ttfb_metrics()
                    yield frame
        except Exception as e:
            yield ErrorFrame(error=f"VoxCPM request failed: {e}")
        finally:
            await self.stop_ttfb_metrics()
