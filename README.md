# qBittorrent Discord Bot

Polls the qBittorrent Web API every 10–15 seconds and keeps a single Discord
embed up-to-date with your download queue.  Also posts one-off notifications
when a torrent is added or completes.

## Features

- **Live summary embed** — edits itself in-place so your channel stays clean
- **New torrent alerts** — notified the moment a torrent is added
- **Completion alerts** — notified when a download finishes
- Runs entirely in Docker, no Python installation required on the host

## Quick Start

### 1. Clone / copy the project

```bash
cd ~/qbit-discord-bot
```

### 2. Create your `.env` file

```bash
cp .env.example .env
nano .env   # fill in your values
```

| Variable         | Description                                      |
|------------------|--------------------------------------------------|
| `QBIT_HOST`      | Full URL to qBittorrent Web UI (with port)       |
| `QBIT_USER`      | qBittorrent Web UI username                      |
| `QBIT_PASS`      | qBittorrent Web UI password                      |
| `DISCORD_WEBHOOK`| Discord incoming webhook URL                     |
| `POLL_INTERVAL`  | Seconds between polls (default: `12`)            |

### 3. Get a Discord Webhook URL

1. Open your Discord server settings
2. Go to **Integrations → Webhooks → New Webhook**
3. Choose the channel, copy the URL, paste it into `.env`

### 4. Start the bot

```bash
docker compose up -d
```

### Useful commands

```bash
# View live logs
docker compose logs -f

# Restart
docker compose restart

# Stop
docker compose down
```

## Project Layout

```
qbit-discord-bot/
├── bot.py              # Main polling script
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## How It Works

1. On startup the bot logs into the qBittorrent Web API and posts an initial
   summary embed to Discord, storing the message ID.
2. Every `POLL_INTERVAL` seconds it fetches the full torrent list.
3. New torrents → one-off "added" embed posted to Discord.
4. Torrents that transition to a completed state → one-off "complete" embed.
5. The persistent summary embed is patched (PATCH `/messages/{id}`) with the
   latest queue state.
6. If the summary message is deleted, a new one is created automatically.
