#!/usr/bin/env python3
"""
Trigger a single photo capture from Omi Glass over BLE and save the JPEG.

Usage: python3 capture_photo.py
  - Start BEFORE or immediately after powering on the glasses.
  - Waits for any in-progress BLE transfer to complete, then triggers a fresh capture.

BLE protocol:
  Service:       19B10000-E8F2-537E-4F6C-D104768A1214
  Photo Data:    19B10005-...  (notify) — 2-byte LE frame index then JPEG bytes
  Photo Control: 19B10006-...  (write)  — 0xFF triggers single capture
  End marker:    notification of exactly [0xFF, 0xFF] — signals end of one photo
"""

import asyncio
import struct
from datetime import datetime
from pathlib import Path
from bleak import BleakClient, BleakScanner

PHOTO_DATA_UUID    = "19B10005-E8F2-537E-4F6C-D104768A1214"
PHOTO_CONTROL_UUID = "19B10006-E8F2-537E-4F6C-D104768A1214"
CMD_SINGLE = bytes([0xFF])

device_found = asyncio.Event()
found_device = None

# Two-phase state machine:
#   phase 0 — draining any in-progress transfer (wait for FF FF)
#   phase 1 — collecting our triggered photo (wait for FF FF)
phase = 0
chunks: dict[int, bytearray] = {}
photo_done = asyncio.Event()
prior_done = asyncio.Event()


def on_photo_data(_, data: bytearray) -> None:
    global phase
    data = bytearray(data)

    if len(data) == 2 and data[0] == 0xFF and data[1] == 0xFF:
        if phase == 0:
            print("Prior transfer complete — ready to trigger.")
            prior_done.set()
        else:
            print("End marker — photo complete.")
            photo_done.set()
        return

    if phase != 1 or len(data) < 3:
        return

    idx = struct.unpack_from("<H", data, 0)[0]
    # Frame 0 has a 3-byte header: [idx_lo, idx_hi, orientation, ...jpeg]
    # All others have a 2-byte header: [idx_lo, idx_hi, ...jpeg]
    payload = data[3:] if idx == 0 else data[2:]
    chunks.setdefault(idx, bytearray()).extend(payload)
    total = sum(len(v) for v in chunks.values())
    print(f"  frame={idx} +{len(payload)}B  total={total}B", flush=True)


def on_detection(device, adv) -> None:
    global found_device
    if device.name == "OMI Glass" and not device_found.is_set():
        print(f"Found OMI Glass  RSSI={adv.rssi}")
        found_device = device
        device_found.set()


async def main() -> None:
    global phase

    print("Watching for 'OMI Glass' — power on the glasses now.")
    async with BleakScanner(detection_callback=on_detection):
        try:
            await asyncio.wait_for(device_found.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Not found within 60s. Exiting.")
            return

        assert found_device is not None
        print("Connecting...")
        try:
            async with BleakClient(found_device, timeout=15) as client:
                print("Connected. Subscribing to photo data...")
                await client.start_notify(PHOTO_DATA_UUID, on_photo_data)

                # Phase 0: drain any in-progress interval capture
                print("Waiting for any in-progress transfer to finish (up to 3 min)...")
                try:
                    await asyncio.wait_for(prior_done.wait(), timeout=180)
                except asyncio.TimeoutError:
                    print("No prior transfer detected, proceeding anyway.")

                # Phase 1: trigger and collect our photo
                phase = 1
                await client.write_gatt_char(PHOTO_CONTROL_UUID, CMD_SINGLE, response=True)
                print("Capture triggered. Collecting photo (up to 3 min)...")
                try:
                    await asyncio.wait_for(photo_done.wait(), timeout=180)
                except asyncio.TimeoutError:
                    print("Timed out. Saving partial data.")

        except Exception as e:
            print(f"Error: {e}")
            return

    if not chunks:
        print("No photo data received.")
        return

    jpeg = bytearray()
    for k in sorted(chunks.keys()):
        jpeg.extend(chunks[k])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(f"omi_capture_{ts}.jpg")
    out.write_bytes(jpeg)
    print(f"\nSaved {len(jpeg):,} bytes → {out}")
    print(f"Resolution: ", end="")
    import subprocess
    r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(out)],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "pixel" in line:
            print(line.strip(), end="  ")
    print()


if __name__ == "__main__":
    asyncio.run(main())
