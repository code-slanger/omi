# Pi Setup & Deploy

Pi 5 at `lucho@192.168.0.27`. The service runs as a Docker container.

---

## First Time

### 1. Install Docker on Pi

```bash
ssh lucho@192.168.0.27
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker lucho
# re-login for group to take effect
```

### 2. Create data directories on Pi

```bash
ssh lucho@192.168.0.27
mkdir -p ~/omi/data ~/omi/chroma ~/obsidian ~/obsidian/attachments
```

### 3. Configure the service

```bash
# On your machine (not Pi):
cd pi
cp service/.env.example service/.env
```

Edit `service/.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...

# Whisper: tiny | base | small | medium | large-v3
WHISPER_MODEL=base

# Creative writer (Alchemist OS)
GENERATION_MODEL=claude-opus-4-6

# Cognitive agent (Nano Claw)
NANO_CLAW_MODEL=claude-haiku-4-5-20251001
NANO_CLAW_USER_ID=mitch

# Omi webhook noise filter
OMI_MIN_WORDS=10

# Obsidian vault path on Pi
OBSIDIAN_VAULT_PATH=/home/lucho/obsidian

# Telegram bot — message @BotFather → /newbot → paste token here
TELEGRAM_BOT_TOKEN=
```

### 4. Deploy

```bash
cd pi
./deploy.sh
```

This creates a Docker SSH context pointing at the Pi, copies `.env`, builds the image on the Pi, and starts the container.

---

## Subsequent Deploys

```bash
cd pi && ./deploy.sh
```

Rebuilds only what changed. Override host:

```bash
PI_HOST=mitch@192.168.0.27 ./deploy.sh
```

---

## Managing the Service

```bash
# Live logs
ssh lucho@192.168.0.27 'docker compose -f ~/... logs -f'
# simpler:
docker --context lucho compose logs -f

# Restart
ssh lucho@192.168.0.27 'docker restart $(docker ps -q)'

# Stop
docker --context lucho compose down

# Shell into container
ssh lucho@192.168.0.27 'docker exec -it $(docker ps -q) bash'
```

---

## Health Check

```bash
curl http://192.168.0.27:8000/health
# → {"status":"ok","model":"claude-opus-4-6"}
```

---

## Connect Omi Glasses

In the Omi iPhone app: **Settings → Developer → Webhook URL**

```
http://192.168.0.27:8000/webhooks/omi/your-name
```

Transcripts from the glasses will hit this endpoint, get processed by Whisper locally, then generated into prose by Claude. Output saved to `~/omi/data/outputs/your-name/` on Pi.

---

## Ingest Your Writing Corpus

SSH to Pi and run against the local service (no network hop):

```bash
ssh lucho@192.168.0.27
cd ~/omi
python ingest.py ~/your-writing-folder --user-id your-name --source-type own_writing
curl -X POST http://localhost:8000/users/your-name/profile/rebuild
```

Or upload individual files from any device on the network:

```bash
curl -X POST http://192.168.0.27:8000/users/your-name/upload \
  -F "file=@essay.md" \
  -F "source_type=own_writing"
```

---

## Data Locations (on Pi)

| Path | Contents |
|------|---------|
| `~/omi/data/uploads/` | Raw corpus files |
| `~/omi/data/profiles/` | Creative profiles (JSON) |
| `~/omi/data/outputs/` | Generated prose (Omi webhook output) |
| `~/omi/chroma/` | Vector embeddings (ChromaDB) |

These persist across restarts and redeploys.

Wipe embeddings (files kept):
```bash
curl -X DELETE http://192.168.0.27:8000/users/your-name/corpus
```

---

## Whisper Model Sizes

| Model | Size | Notes |
|-------|------|-------|
| `tiny` | 75MB | Fastest |
| `base` | 145MB | Good default |
| `small` | 466MB | Better accuracy |
| `medium` | 1.5GB | High accuracy |
| `large-v3` | 3GB | Best, slower on Pi |

Change `WHISPER_MODEL` in `service/.env` and redeploy.
