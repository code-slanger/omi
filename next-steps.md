# Next Steps — Vault + Nano Claw Setup

## 1. Obsidian Vault (operational-lair)
- [ ] Open `/Users/mitchumbailey/operational-lair` as an Obsidian vault
- [ ] Verify `.obsidianignore` is hiding code dirs (omi/pi, omi/omi-fw, **/code)
- [ ] Go through `personal/_private/` and move anything NOT private up to `personal/`
- [ ] Decide what to do with `mitchum/` submodule (currently just has index.html)

## 2. obsidian-headless — Pi sync (uses your existing Obsidian Sync subscription)

Install on Pi (requires Node.js 22+):
```bash
# Install Node 22 if needed
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

npm install -g obsidian-headless
```

Setup:
```bash
ob login
ob sync-setup --vault "operational-lair" --path /home/lucho/vault --device-name "pi"
ob sync-config --excluded-folders "personal/_private"   # keeps private notes off Pi
ob sync --continuous                                     # test it works
```

Run as a systemd service:
```ini
# /etc/systemd/system/obsidian-sync.service
[Unit]
Description=Obsidian Headless Sync
After=network-online.target

[Service]
User=lucho
ExecStart=/usr/bin/ob sync --continuous --path /home/lucho/vault
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now obsidian-sync
```

- [ ] Install Node 22 + obsidian-headless on Pi
- [ ] `ob login` and `ob sync-setup`
- [ ] Exclude `personal/_private` via `ob sync-config`
- [ ] Verify vault appears at `/home/lucho/vault` on Pi
- [ ] Enable systemd service

## 3. Obsidian Sync — cross-device (iPhone, iPad, other Macs)
- [ ] Point Obsidian Sync at `operational-lair` as the single vault
- [ ] Remove luchos-lair as a separate synced vault once you're happy with the migration

## 4. Vault Watcher on Pi
- [ ] Add `OBSIDIAN_VAULT_PATH=/home/lucho/vault` to Pi `.env`
- [ ] Configure watcher to exclude `_private/` folder from ChromaDB indexing
- [ ] Test: edit a note on Mac → confirm it appears in ChromaDB on Pi within ~30s

## 5. Nano Claw — Agent + Telegram
- [ ] Build `pi/service/src/nano_claw/vault_watcher.py`
- [ ] Build `pi/service/src/nano_claw/agent.py` (Haiku + tools: retrieve, write_note, list_projects)
- [ ] Build `pi/service/src/nano_claw/telegram.py`
- [ ] Add `TELEGRAM_BOT_TOKEN` to Pi `.env`
- [ ] Create a Telegram bot via @BotFather, get token
- [ ] Wire router into Omi webhook (`/webhooks/omi/{user_id}`)

## 6. Project Tracker
- [ ] Create `operational-lair/omi/project.md`, `locus/project.md` etc. as structured project status notes
- [ ] Build `git_watcher.py` — polls each `*/code` repo for recent commits, updates project notes
- [ ] Set up morning briefing cron: daily Telegram digest of all project statuses

## 7. omi Restructure (do last — breaks existing paths)
- [ ] Move `omi/pi`, `omi/omi-fw`, `omi/docs` → `omi/code/`
- [ ] Update `.stignore`: remove explicit `omi/pi` and `omi/omi-fw` lines (covered by `code` rule)
- [ ] Update `.obsidianignore` same
- [ ] Update `deploy.sh` paths if affected

## 8. Finance Weekly Review

Pull spending/income into the vault as a weekly note, delivered via Telegram.

**Data sources (pick one to start):**
- **Plaid** — connects most US banks + cards in one OAuth flow; gives transactions, merchant names, categories
- **Teller** — simpler auth, real-time at more banks; good Plaid alternative

**What to build:**
- [ ] `pi/service/src/nano_claw/finance.py` — fetches last 7 days of transactions via Plaid/Teller API
- [ ] Writes `finance/YYYY-WW.md` to the vault (spend by category, total in vs out, largest transactions)
- [ ] Add `PLAID_CLIENT_ID`, `PLAID_SECRET` (or `TELLER_CERT`) to Pi `.env`
- [ ] Wire into the morning briefing cron — run on Mondays, covering the prior week
- [ ] Add a weekly total line to the Telegram digest alongside project statuses

**Note:** Plaid sandbox is free for dev. You'll need a production key (free tier) to connect real accounts.

## Notes
- `personal/_private/` is never indexed by the vault watcher or ingested into ChromaDB
- Once the `code` convention is established, new projects just follow it — no extra ignore rules needed
- Syncthing handles Pi sync; Obsidian Sync handles mobile/other devices — they don't conflict
