# Todo

## Done ✅

- [x] Glasses post photos over WiFi directly to Pi (no Omi app)
- [x] Glasses post audio over WiFi with VAD — no Omi app needed
- [x] Whisper transcription on Pi
- [x] Nano Claw (Haiku + tools) routes audio to Telegram
- [x] Wake word filtering ("hey man") — strips wake word before routing
- [x] Whisper forced to English — no more Dutch/mixed-language responses
- [x] Nano Claw always responds in English
- [x] Photo pruning — deletes JPEGs older than 3 days
- [x] Radicale CalDAV server running on Pi (port 5232)
- [x] Calendar: `get_calendar_events` + `add_calendar_event` tools wired to Radicale
- [x] Tasks: `create_task` tool writes Obsidian Tasks plugin format to vault
- [x] Web search fixed (ddgs package)
- [x] Socket leak fixed — WiFi connections more stable

## Immediate: Calendar & Obsidian Integration

- [ ] **Connect Obsidian Full Calendar plugin to Radicale**
      - Install Full Calendar plugin in Obsidian
      - Add CalDAV source: `http://192.168.0.27:5232/mitch/main/`
      - User: `mitch` / Password: `omiCalendar2026`
- [ ] **Sync phone calendar to Radicale**
      - iOS: Settings → Calendar → Accounts → Add Account → Other → CalDAV
        Server: `http://192.168.0.27:5232`, user: `mitch`, password: `omiCalendar2026`
      - Android: install DAVx⁵, same credentials
- [ ] **Install Obsidian Tasks plugin** — picks up `- [ ] Task 📅 YYYY-MM-DD` format
      created by Nano Claw's `create_task` tool
- [ ] **Test voice calendar flow end-to-end**
      - "hey man, what do I have on today" → reads Radicale
      - "hey man, remind me to call the dentist on Friday" → creates task in vault
      - "hey man, add a meeting with James tomorrow at 2pm" → creates CalDAV event

## Outside Home Network (Tailscale)

The entire stack (glasses → Pi → Telegram) currently only works on the home network.
To use from anywhere:

- [ ] **Install Tailscale on the Pi**
      ```bash
      ssh lucho@192.168.0.27
      curl -fsSL https://tailscale.com/install.sh | sh
      sudo tailscale up
      ```
      Note the Pi's Tailscale IP (e.g. `100.x.x.x`)

- [ ] **Update glasses firmware with Tailscale IP**
      In `omi-fw/omiGlass/firmware/src/config.h`:
      ```c
      #define PI_SERVICE_URL "http://100.x.x.x:8000"
      ```
      Reflash via USB. NVS WiFi credentials survive reflash.

- [ ] **Expose Radicale over Tailscale**
      Once Tailscale is running, update Obsidian Full Calendar + phone to use
      `http://100.x.x.x:5232` instead of the local IP.

- [ ] **Install Tailscale on phone/laptop** to access Pi services remotely

- [ ] **Test outside home** — glasses on a different network, confirm
      audio → Pi → Telegram works end-to-end

## Firmware Stability

- [ ] **WiFi long-session stability** — glasses drop WiFi after extended use.
      Need a reconnection watchdog in the firmware loop: call `WiFi.reconnect()`
      if uploads fail N times in a row.
- [ ] **Noisy environment fallback** — VAD struggles in loud rooms.
      Consider a hardware button trigger (long press) as an alternative to VAD.

## Next

- [ ] ESP32 watch — biometric data feed into pipeline
- [ ] Proactive agent surfacing context based on conversation history
- [ ] Multi-agent routing — different wake words route to different agents
      (e.g. "hey man" → Nano Claw, another phrase → Alchemist creative agent)
