# Todo

## Immediate: Omi Self-Hosted on Pi 5

- [ ] Clone Omi repo on Pi 5
- [ ] Audit backend codebase — map all Firebase/cloud dependencies
- [ ] Replace Firebase → Supabase (self-hosted via Docker)
- [ ] Replace Deepgram → Whisper (local STT)
- [ ] Replace OpenAI → Ollama (local LLM)
- [ ] Replace Pinecone → pgvector (Postgres extension, included with Supabase)
- [ ] Wire Obsidian as memory/transcript output
- [ ] Fork Omi mobile app and point it at local Pi backend
- [ ] Test end-to-end: glasses → Pi → Obsidian

## Next

- [ ] ESP32 watch — biometric data feed into pipeline
- [ ] Agent triggered from glasses interface
- [ ] Proactive agent surfacing context based on biometrics + conversation history
