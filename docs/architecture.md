# Architecture

## Principle

Everything runs on the Pi or the glasses. iPhone is a BLE bridge (Omi app) and a second input surface (Telegram). No Mac in the operational loop.

## System Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Omi Glasses                                  │
│  always-on audio • JPEG camera • agent interface                     │
└─────────────────────────┬────────────────────────────────────────────┘
                          │ BLE
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│               iPhone (BLE bridge + Telegram client)                  │
│                                                                      │
│  Omi app → transcribes glasses audio → POST /webhooks/omi/{user}     │
│  Telegram → text, voice notes, photos, videos → Pi bot               │
└─────────────┬─────────────────────────┬────────────────────────────-─┘
              │ HTTP (local WiFi)        │ Telegram API (internet)
              ▼                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Raspberry Pi 5  (192.168.0.27)                      │
│                                                                      │
│  pi/service/  (:8000, Docker)                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                                                              │   │
│  │   ┌─────────────────────────────────────────────────────┐   │   │
│  │   │              Mode Router                            │   │   │
│  │   │  Classifies intent → creative or cognitive          │   │   │
│  │   │  1. Explicit command (/create, /note, /todo…)       │   │   │
│  │   │  2. Keyword fast-path                               │   │   │
│  │   │  3. Haiku classification                            │   │   │
│  │   └──────────┬──────────────────────┬───────────────────┘   │   │
│  │              │ creative              │ cognitive             │   │
│  │              ▼                       ▼                       │   │
│  │   ┌──────────────────┐   ┌──────────────────────────────┐   │   │
│  │   │  Alchemist OS    │   │       Nano Claw              │   │   │
│  │   │  Writer Agent    │   │   Cognitive Agent            │   │   │
│  │   │                  │   │                              │   │   │
│  │   │  claude-opus-4-6 │   │  claude-haiku-4-5            │   │   │
│  │   │  adaptive think  │   │  tool use:                   │   │   │
│  │   │  RAG on writing  │   │    retrieve_context          │   │   │
│  │   │  corpus          │   │    write_note                │   │   │
│  │   │                  │   │    list_recent_notes         │   │   │
│  │   │  → prose in your │   │  → Obsidian vault            │   │   │
│  │   │    own voice     │   │    notes, todos, emails      │   │   │
│  │   └──────────────────┘   └──────────────────────────────┘   │   │
│  │                                                              │   │
│  │   faster-whisper    — local STT, no API key                 │   │
│  │   ChromaDB          — RAG vector store                      │   │
│  │   sentence-transformers — embeddings                        │   │
│  │   Telegram bot      — polling, handles all media types      │   │
│  │   Vault watcher     — auto-indexes new Obsidian .md files   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Storage (persisted Docker volumes):                                 │
│    ~/omi/data/     — uploads, profiles, generated outputs            │
│    ~/omi/chroma/   — vector embeddings                               │
│    ~/obsidian/     — Obsidian vault (notes written by agent)         │
└──────────────────────────────────────────────────────────────────────┘
```

## Two Interfaces, Two Modes

### Interfaces

| Interface | How it reaches Pi | Media types |
|-----------|------------------|-------------|
| Omi glasses | Omi app → HTTP webhook | Transcribed speech |
| Telegram (iPhone) | Telegram API → bot polling | Text, voice notes, photos, videos |

### Modes

| Mode | When | Agent | Output |
|------|------|-------|--------|
| **Creative** (Alchemist OS) | Writing, prose, poems, scenes, lyrics | `claude-opus-4-6` + writing corpus RAG | Prose in your voice |
| **Cognitive** (Nano Claw) | Todos, notes, emails, questions, reminders | `claude-haiku-4-5` + Obsidian tools | Practical response + Obsidian note |

Mode is determined automatically. Override with slash commands.

## Data Flow: Voice → Prose (Creative)

1. Speak into glasses
2. Omi app → `POST /webhooks/omi/{user}` with transcript
3. Router classifies → creative
4. Writer agent retrieves 5 nearest writing corpus chunks (ChromaDB)
5. Opus generates prose grounded in your voice
6. Saved to `~/omi/data/outputs/{user}/`

## Data Flow: Task → Obsidian (Cognitive)

1. Send "todo: call dentist" to Telegram (or say it to glasses)
2. Router classifies → cognitive
3. Haiku agent calls `write_note` tool
4. Note saved to `~/obsidian/call dentist.md`
5. Vault watcher picks it up → indexed into ChromaDB
6. Future retrieval can find this note by content

## Data Flow: Telegram Media

```
Voice note → download OGG → Whisper transcribe → router → agent
Photo      → save to ~/obsidian/attachments/ → caption → cognitive agent
Video      → download → ffmpeg extract audio → Whisper → router → agent
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Glasses | Omi (BLE, Opus audio, JPEG) |
| iPhone | Omi app (BLE bridge) + Telegram |
| STT | faster-whisper (local, Pi CPU) |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` |
| Vector store | ChromaDB (local) |
| Creative LLM | `claude-opus-4-6` (adaptive thinking) |
| Cognitive LLM | `claude-haiku-4-5-20251001` (tool use) |
| API | FastAPI + uvicorn |
| Telegram | python-telegram-bot (async polling) |
| Vault sync | watchfiles (asyncio) |
| Container | Docker |
