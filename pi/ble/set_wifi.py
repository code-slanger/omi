#!/usr/bin/env python3
"""
Send WiFi credentials to OMI Glass over BLE.

The glasses save them to NVS and connect immediately.
Credentials survive power cycles — only needs to be run once.

Usage:
  uv run set_wifi.py                        # prompts for SSID and password
  uv run set_wifi.py MyNetwork MyPassword   # pass directly
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient, BleakScanner

DEVICE_NAME      = "OMI Glass"
OTA_CONTROL_UUID = "19B10011-E8F2-537E-4F6C-D104768A1214"
OTA_DATA_UUID    = "19B10012-E8F2-537E-4F6C-D104768A1214"
OTA_CMD_SET_WIFI = 0x01

STATUS = {
    0x00: "IDLE (credentials saved, connecting...)",
    0x10: "WiFi connecting",
    0x11: "WiFi connected",
    0x12: "WiFi failed",
    0xFF: "Error",
}


def build_set_wifi_payload(ssid: str, password: str) -> bytes:
    ssid_b = ssid.encode()
    pass_b = password.encode()
    return bytes([OTA_CMD_SET_WIFI, len(ssid_b)]) + ssid_b + bytes([len(pass_b)]) + pass_b


async def run(ssid: str, password: str) -> None:
    print(f"Scanning for '{DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=20)
    if not device:
        print(f"'{DEVICE_NAME}' not found. Make sure glasses are on and in range.")
        sys.exit(1)

    print(f"Found {device.name} ({device.address}) — connecting...")

    status_event = asyncio.Event()

    def on_notify(_, data: bytearray) -> None:
        code = data[0] if data else 0xFF
        label = STATUS.get(code, f"0x{code:02X}")
        print(f"  Status: {label}")
        status_event.set()

    async with BleakClient(device, timeout=15) as client:
        await asyncio.sleep(1.0)  # let connection settle

        # Subscribe to status notifications (best-effort)
        try:
            await client.start_notify(OTA_DATA_UUID, on_notify)
        except Exception as e:
            print(f"  (Notify subscribe skipped: {e})")

        payload = build_set_wifi_payload(ssid, password)
        print(f"Sending WiFi credentials (SSID: {ssid}, {len(payload)} bytes)...")

        try:
            await client.write_gatt_char(OTA_CONTROL_UUID, payload, response=True)
        except Exception:
            # NimBLE sometimes rejects write-with-response; try without
            await client.write_gatt_char(OTA_CONTROL_UUID, payload, response=False)

        # Wait up to 5 s for the acknowledgement
        try:
            await asyncio.wait_for(status_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            print("  No status notification received (glasses may still be processing)")

        try:
            await client.stop_notify(OTA_DATA_UUID)
        except Exception:
            pass

    print("Done. Glasses will connect to WiFi and remember credentials across reboots.")


def main() -> None:
    if len(sys.argv) == 3:
        ssid, password = sys.argv[1], sys.argv[2]
    else:
        import getpass
        ssid     = input("WiFi SSID: ").strip()
        password = getpass.getpass("WiFi password: ")

    if not ssid:
        print("SSID cannot be empty.")
        sys.exit(1)

    asyncio.run(run(ssid, password))


if __name__ == "__main__":
    main()
