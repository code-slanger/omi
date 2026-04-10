#!/usr/bin/env python3
"""
Stream audio from Omi Glass over BLE and play it live.

Audio format (from firmware config):
  BLE characteristic: 19B10001-E8F2-537E-4F6C-D104768A1214 (notify)
  Codec:              Opus, 32kbps, 16kHz mono, 20ms frames (320 samples)
  Packet header:      [idx_lo, idx_hi, sub_idx] (3 bytes), then Opus payload

Usage: uv run audio_stream.py
"""

import asyncio
import ctypes.util
import queue
import threading

# ctypes.util.find_library('opus') returns None on macOS with Homebrew.
# Patch it before opuslib imports so it gets the correct path.
_orig_find_library = ctypes.util.find_library
def _find_library_patched(name: str) -> str | None:
    if name == "opus":
        return "/opt/homebrew/lib/libopus.dylib"
    return _orig_find_library(name)
ctypes.util.find_library = _find_library_patched

import numpy as np
import opuslib
import sounddevice as sd
from bleak import BleakClient, BleakScanner

AUDIO_DATA_UUID  = "19B10001-E8F2-537E-4F6C-D104768A1214"
AUDIO_CODEC_UUID = "19B10002-E8F2-537E-4F6C-D104768A1214"

SAMPLE_RATE    = 16000
CHANNELS       = 1
FRAME_SAMPLES  = 320   # 20ms @ 16kHz

device_found   = asyncio.Event()
found_device   = None
audio_queue: queue.Queue = queue.Queue(maxsize=50)


def audio_player() -> None:
    """Runs in a thread: pulls PCM frames from the queue and plays them."""
    decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
    stream  = sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                               dtype="int16", blocksize=FRAME_SAMPLES)
    stream.start()
    print("Audio output started.")
    while True:
        opus_payload = audio_queue.get()
        if opus_payload is None:
            break
        try:
            pcm = decoder.decode(bytes(opus_payload), FRAME_SAMPLES)
            samples = np.frombuffer(pcm, dtype=np.int16)
            stream.write(samples)
        except Exception as e:
            print(f"  decode error: {e}", flush=True)
    stream.stop()
    stream.close()


def on_audio_data(_, data: bytearray) -> None:
    data = bytearray(data)
    if len(data) < 4:
        return
    # Header: [idx_lo, idx_hi, sub_idx] — skip 3 bytes
    opus_payload = data[3:]
    if audio_queue.full():
        audio_queue.get_nowait()  # drop oldest to avoid buildup
    audio_queue.put_nowait(opus_payload)


def on_detection(device, adv) -> None:
    global found_device
    if device.name == "OMI Glass" and not device_found.is_set():
        print(f"Found OMI Glass  RSSI={adv.rssi}")
        found_device = device
        device_found.set()


async def main() -> None:
    print("Watching for 'OMI Glass'...")

    # Start audio player thread
    player = threading.Thread(target=audio_player, daemon=True)
    player.start()

    async with BleakScanner(detection_callback=on_detection):
        try:
            await asyncio.wait_for(device_found.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Not found within 60s.")
            audio_queue.put(None)
            return

        assert found_device is not None
        print("Connecting...")
        async with BleakClient(found_device, timeout=15) as client:
            # Confirm codec
            codec_raw = await client.read_gatt_char(AUDIO_CODEC_UUID)
            print(f"Codec ID reported by glasses: {codec_raw.hex()} "
                  f"(expected 15=0x15 for Opus)")

            print("Subscribing to audio stream — speak into the glasses. Ctrl-C to stop.")
            await client.start_notify(AUDIO_DATA_UUID, on_audio_data)
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping.")
            await client.stop_notify(AUDIO_DATA_UUID)

    audio_queue.put(None)
    player.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
