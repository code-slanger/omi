# Cognitive & Creative OS

A fully local, privacy-first cognitive and creative operating system built on wearable hardware.

The system passively captures life context through Omi glasses, builds a persistent knowledge graph in Obsidian, and runs AI agents that augment both cognition and creative output in real time — all on a Raspberry Pi 5 you own.

## The Two Layers

### Cognitive OS

Always-on wearables feed continuous context into a local AI stack. Everything is stored in Obsidian as a living second brain. An AI agent reasons over this knowledge graph and either surfaces proactive insight or responds to queries at the right moment.

- **Glasses (Omi)** — audio capture, transcription, real-time agent interface
- **Nano Claw** — command interface on the Pi for triggering actions from the glasses: send emails, take notes, transcribe conversations
- **Pi 5** — local inference and orchestration hub; Obsidian vault + RAG database live here
- **Watch (ESP32)** — biometrics, activity tracking, gestures, haptic feedback

### Alchemist OS (Creative OS)

The creative layer trains on your existing writing, voice, photos, and audio — then generates new work that sounds authentically like you. Speak a rough idea into the glasses; get back a passage in your voice, saved directly to Obsidian.

Extended ambitions:
- **Write books** from the glasses in your own voice
- **Script and narrate documentaries** from field recordings and voice notes
- **Generate film treatments** from raw ideas spoken on the go

## How It Fits Together

```
Omi glasses ──BLE──> Pi 5 ─────────────────────────────────────────────>
                      │
                      ├── Obsidian vault (Markdown knowledge graph)
                      ├── RAG database (ChromaDB — efficient retrieval)
                      ├── Whisper (local STT — fully offline)
                      ├── Ollama (local LLM — no API keys needed)
                      └── Alchemist OS (personalisation-service)
                              ├── Voice → transcript → writing in your voice
                              ├── Corpus ingestion (your existing work)
                              └── Creative profile (your aesthetic fingerprint)
```

## What Makes This Different

- **Fully local** — no data leaves your network; no subscriptions
- **Your voice, permanently** — creative output trained on your actual writing, not a generic style
- **Physiological context** — biometrics from the watch tag every note with your cognitive/physical state
- **Persistent memory** — Obsidian as the knowledge graph the agent reasons over indefinitely
- **Proactive intelligence** — agent surfaces the right context without being queried
- **Open hardware** — every layer is hackable and extensible

## Expanding Over Time

- Nano Claw automations: email from glasses, calendar management, smart home control
- ESP32 mesh network for seamless home/office coverage
- Edge inference on-device for ultra-low latency
- Longitudinal self-modelling — patterns across months of data
- Documentary and film pipeline: field audio → structured narrative → final script
- Custom haptic language as a back-channel from the agent to the watch
