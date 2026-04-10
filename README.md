# Omi — Cognitive & Creative OS

Fully local cognitive and creative OS on Omi glasses + Raspberry Pi 5. Everything runs on the Pi or the glasses. No Mac in the loop.

See [`what.md`](what.md) for the full vision.

---

## Structure

```
omi/
├── pi/                  # Everything on Raspberry Pi 5
│   ├── service/         # FastAPI backend — Whisper, ChromaDB, Claude
│   ├── ble/             # BLE tools — audio stream, photo capture (Pi 5 Bluetooth)
│   ├── samples/         # Drop writing samples here for ingestion
│   ├── ingest.py        # Corpus ingestion (run on Pi)
│   ├── docker-compose.yml
│   └── deploy.sh        # One-command deploy
└── omi-fw/              # Omi glasses firmware (ESP32)
```

---

## Deploy

```bash
cd pi
cp service/.env.example service/.env
# set ANTHROPIC_API_KEY in service/.env
./deploy.sh
```

Service: `http://192.168.0.27:8000` — `curl http://192.168.0.27:8000/health`

Full guide: [`docs/pi-deploy.md`](docs/pi-deploy.md)

---

## Data Flow

```
Omi glasses
    └── Omi iPhone app (BLE bridge)
            └── POST /webhooks/omi/{user}  ──> Pi :8000
                                                  ├── Whisper (transcribe)
                                                  ├── ChromaDB (retrieve your writing)
                                                  ├── Claude (generate in your voice)
                                                  └── /home/lucho/omi/data/ (save output)
```

**No Mac. No intermediate steps. Glasses → iPhone Omi app → Pi.**

---

## Ingesting Your Writing Corpus

SSH into the Pi and run directly against the local service:

```bash
ssh lucho@192.168.0.27
cd ~/omi
python ingest.py ~/obsidian --user-id mitch --source-type own_writing
# then rebuild your creative profile:
curl -X POST http://localhost:8000/users/mitch/profile/rebuild
```

Or use the upload API from any device on the network:

```bash
curl -X POST http://192.168.0.27:8000/users/mitch/upload \
  -F "file=@mywriting.md" -F "source_type=own_writing"
```

---

## BLE Tools (Pi 5 Bluetooth)

The Pi 5 has built-in Bluetooth. These run directly on the Pi:

```bash
cd ~/omi/ble
uv sync
uv run audio_stream.py    # stream live audio from glasses
uv run capture_photo.py   # trigger photo capture
```

---

## Docs

- [Architecture](docs/architecture.md)
- [Pi Setup & Deploy](docs/pi-deploy.md)
- [Service API](docs/api.md)
