#!/usr/bin/env python3
"""
Omi Glass BLE bridge — Pi native.

Connects directly to the glasses over Bluetooth, streams Opus audio,
segments by silence, decodes to WAV, and POSTs each utterance to the
local service for transcription + routing.

Prerequisites (on Pi):
  sudo apt install -y libopus0
  uv sync   (inside pi/ble/)

Usage:
  uv run ble_bridge.py
  uv run ble_bridge.py --user-id default --min-speech-secs 1.5 --silence-secs 1.5
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import wave

import httpx
import numpy as np
import opuslib
from bleak import BleakClient, BleakScanner

# ── BLE UUIDs ─────────────────────────────────────────────────────────────────
AUDIO_DATA_UUID  = "19B10001-E8F2-537E-4F6C-D104768A1214"
AUDIO_CODEC_UUID = "19B10002-E8F2-537E-4F6C-D104768A1214"
DEVICE_NAME      = "OMI Glass"

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE   = 16000
CHANNELS      = 1
FRAME_SAMPLES = 320   # 20ms @ 16kHz

# ── Silence detection ─────────────────────────────────────────────────────────
# RMS below this threshold = silence  (tune if needed: 0–32767 scale)
SILENCE_THRESHOLD = 200


def frames_to_wav(pcm_frames: list[np.ndarray]) -> bytes:
    """Pack PCM frames into an in-memory WAV file and return bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        for frame in pcm_frames:
            wf.writeframes(frame.tobytes())
    return buf.getvalue()


async def post_audio(wav_bytes: bytes, user_id: str, service_url: str) -> None:
    """POST WAV to the service and print the result."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{service_url}/users/{user_id}/process-audio",
            files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
        )
        if resp.status_code == 200:
            data = resp.json()
            mode = data.get("mode", "?")
            transcript = data.get("transcript", "")
            text = data.get("text", "")
            print(f"\n[{mode.upper()}] Transcript: {transcript}")
            print(f"→ {text}\n")
        else:
            print(f"Service error {resp.status_code}: {resp.text[:200]}")


class AudioSegmenter:
    """
    Buffers decoded PCM frames and emits complete utterances.

    An utterance ends when silence_secs of silence follow at least
    min_speech_secs of speech.
    """

    def __init__(
        self,
        silence_secs: float = 1.5,
        min_speech_secs: float = 1.0,
    ):
        self.silence_frames = int(silence_secs * SAMPLE_RATE / FRAME_SAMPLES)
        self.min_speech_frames = int(min_speech_secs * SAMPLE_RATE / FRAME_SAMPLES)

        self._buffer: list[np.ndarray] = []
        self._silent_count = 0
        self._speech_count = 0
        self._ready: list[list[np.ndarray]] = []  # completed utterances

    def push(self, pcm: np.ndarray) -> None:
        rms = int(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        is_silent = rms < SILENCE_THRESHOLD

        self._buffer.append(pcm)

        if is_silent:
            self._silent_count += 1
        else:
            self._speech_count += 1
            self._silent_count = 0

        # End utterance if we had enough speech and now enough silence
        if (
            self._speech_count >= self.min_speech_frames
            and self._silent_count >= self.silence_frames
        ):
            self._ready.append(list(self._buffer))
            self._buffer = []
            self._silent_count = 0
            self._speech_count = 0

    def pop_ready(self) -> list[list[np.ndarray]] | None:
        if self._ready:
            ready = self._ready[:]
            self._ready = []
            return ready
        return None


async def run(user_id: str, service_url: str, silence_secs: float, min_speech_secs: float) -> None:
    decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
    segmenter = AudioSegmenter(silence_secs=silence_secs, min_speech_secs=min_speech_secs)

    # queue for raw BLE packets (thread-safe)
    packet_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    def on_audio_data(_, data: bytearray) -> None:
        if len(data) < 4:
            return
        # Skip 3-byte header [idx_lo, idx_hi, sub_idx]
        opus_payload = bytes(data[3:])
        try:
            packet_queue.put_nowait(opus_payload)
        except asyncio.QueueFull:
            pass  # drop under backpressure

    print(f"Scanning for '{DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=30)
    if not device:
        print(f"'{DEVICE_NAME}' not found. Make sure glasses are on and in range.")
        sys.exit(1)

    print(f"Found {device.name} ({device.address}) — connecting...")
    async with BleakClient(device, timeout=15) as client:
        codec_raw = await client.read_gatt_char(AUDIO_CODEC_UUID)
        codec_id = codec_raw[0] if codec_raw else "?"
        print(f"Codec ID: {codec_id} (expected 21=0x15 for Opus)")
        print(f"Posting to: {service_url}/users/{user_id}/process-audio")
        print("Listening — speak into the glasses. Ctrl-C to stop.\n")

        await client.start_notify(AUDIO_DATA_UUID, on_audio_data)

        try:
            while True:
                # Drain the packet queue and decode
                while not packet_queue.empty():
                    opus_payload = packet_queue.get_nowait()
                    try:
                        pcm_bytes = decoder.decode(opus_payload, FRAME_SAMPLES)
                        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
                        segmenter.push(pcm)
                    except Exception as e:
                        pass  # corrupt frame, skip

                # Check for completed utterances and post them
                utterances = segmenter.pop_ready()
                if utterances:
                    for frames in utterances:
                        duration = len(frames) * FRAME_SAMPLES / SAMPLE_RATE
                        print(f"Utterance detected ({duration:.1f}s) — sending to service...")
                        wav = frames_to_wav(frames)
                        asyncio.create_task(post_audio(wav, user_id, service_url))

                await asyncio.sleep(0.05)

        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            await client.stop_notify(AUDIO_DATA_UUID)


def main() -> None:
    parser = argparse.ArgumentParser(description="Omi Glass BLE bridge")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--service-url", default="http://localhost:8000")
    parser.add_argument("--silence-secs", type=float, default=1.5,
                        help="Seconds of silence to end an utterance (default: 1.5)")
    parser.add_argument("--min-speech-secs", type=float, default=1.0,
                        help="Minimum speech duration to keep (default: 1.0)")
    args = parser.parse_args()

    asyncio.run(run(
        user_id=args.user_id,
        service_url=args.service_url,
        silence_secs=args.silence_secs,
        min_speech_secs=args.min_speech_secs,
    ))


if __name__ == "__main__":
    main()
