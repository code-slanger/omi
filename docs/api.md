# Service API

Base URL: `http://192.168.0.27:8000`

## Core Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/health` | Health check |
| POST | `/webhooks/omi/{user_id}` | **Omi glasses webhook** — receives transcript, generates prose |
| POST | `/users/{user_id}/upload` | Upload a file (text, audio, image, video) |
| GET | `/users/{user_id}/uploads` | List uploaded files |
| POST | `/users/{user_id}/process-audio` | Upload audio → transcribe → generate |
| POST | `/users/{user_id}/generate` | Generate from a text prompt |
| POST | `/users/{user_id}/profile/rebuild` | Rebuild creative profile from corpus |
| GET | `/users/{user_id}/profile` | Get current creative profile |
| POST | `/users/{user_id}/feedback` | Log accept / edit / reject on a generation |
| DELETE | `/users/{user_id}/corpus` | Clear embedding index (files kept) |

---

## Omi Webhook

The primary entry point. Configure this URL in the Omi app.

```
POST /webhooks/omi/{user_id}
```

Payload (sent by Omi app):
```json
{
  "session_id": "abc123",
  "segments": [
    { "text": "...", "speaker": "SPEAKER_00", "start": 0.0, "end": 3.5 }
  ]
}
```

Transcripts shorter than `OMI_MIN_WORDS` (default: 10) are silently ignored.

Response:
```json
{
  "status": "generated",
  "generation_id": "uuid",
  "session_id": "abc123",
  "transcript": "raw transcript text",
  "text": "generated prose in your voice"
}
```

Output also saved to `~/omi/data/outputs/{user_id}/` on Pi.

---

## Upload

```
POST /users/{user_id}/upload?source_type=own_writing
Content-Type: multipart/form-data
```

- `source_type`: `own_writing` (your work) or `reference` (books/notes)

Accepted formats:

| Type | Extensions |
|------|-----------|
| Text | `.md`, `.txt`, `.pdf` |
| Audio | `.mp3`, `.wav`, `.m4a` |
| Image | `.jpg`, `.png`, `.heic` |
| Video | `.mp4`, `.mov` |

---

## Generate

```
POST /users/{user_id}/generate
Content-Type: application/json

{"prompt": "write about the city at dawn"}
```

Returns generated text grounded in the user's corpus and creative profile.

---

## Profile

The creative profile is built by Claude analysing your full corpus:

```json
{
  "text": {
    "voice_summary": "terse, image-heavy, avoids abstraction",
    "common_themes": ["isolation", "urban decay", "ambition"]
  },
  "audio": {
    "sonic_references": ["lo-fi", "boom bap"]
  },
  "visual": {
    "palette": ["desaturated", "warm shadows"],
    "recurring_subjects": ["architecture", "empty spaces"]
  }
}
```

Rebuild after ingesting new content:
```bash
curl -X POST http://192.168.0.27:8000/users/mitch/profile/rebuild
```
