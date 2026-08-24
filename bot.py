"""
qBittorrent → Discord polling bot.

Polls the qBittorrent Web API on a configurable interval and keeps a single
Discord embed up-to-date with the current download queue.  When a torrent
completes or is newly added an additional one-off notification is posted.
"""

import os
import time
import logging
import requests
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------
QBIT_HOST     = os.environ.get("QBIT_HOST", "http://192.168.0.2:8080")
QBIT_USER     = os.environ.get("QBIT_USER", "admin")
QBIT_PASS     = os.environ.get("QBIT_PASS", "adminadmin")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL", "12"))  # seconds

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
# Maps torrent hash → last known state so we can detect transitions.
known_torrents: dict[str, dict] = {}
# Discord message ID of the persistent summary embed (so we can edit it).
summary_message_id: str | None = None

# ---------------------------------------------------------------------------
# qBittorrent helpers
# ---------------------------------------------------------------------------

session = requests.Session()


def qbit_login() -> bool:
    """Authenticate with qBittorrent Web UI.  Returns True on success."""
    url = f"{QBIT_HOST}/api/v2/auth/login"
    try:
        resp = session.post(url, data={"username": QBIT_USER, "password": QBIT_PASS}, timeout=10)
        if resp.text.strip() == "Ok.":
            log.info("Logged in to qBittorrent.")
            return True
        log.error("qBittorrent login failed: %s", resp.text)
        return False
    except requests.RequestException as exc:
        log.error("qBittorrent login error: %s", exc)
        return False


def qbit_get_torrents() -> list[dict]:
    """Fetch all torrents from qBittorrent.  Re-authenticates on 403."""
    url = f"{QBIT_HOST}/api/v2/torrents/info"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 403:
            log.warning("Session expired, re-logging in.")
            qbit_login()
            resp = session.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.error("Failed to fetch torrents: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

STATE_EMOJI = {
    "downloading":     "⬇️",
    "stalledDL":       "⏸️",
    "uploading":       "⬆️",
    "stalledUP":       "⏸️",
    "pausedDL":        "⏸️",
    "pausedUP":        "⏸️",
    "queuedDL":        "🕐",
    "queuedUP":        "🕐",
    "checkingDL":      "🔍",
    "checkingUP":      "🔍",
    "moving":          "📦",
    "error":           "❌",
    "missingFiles":    "❌",
    "forcedDL":        "⬇️",
    "forcedUP":        "⬆️",
    "metaDL":          "🔎",
}


def fmt_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def fmt_speed(bps: float) -> str:
    return fmt_bytes(bps) + "/s"


def fmt_eta(seconds: int) -> str:
    if seconds < 0 or seconds >= 8640000:
        return "∞"
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def build_summary_embed(torrents: list[dict]) -> dict:
    """Build a Discord embed summarising all torrents."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if not torrents:
        return {
            "title": "📭 qBittorrent — No torrents",
            "description": "The download queue is empty.",
            "color": 0x5865F2,
            "footer": {"text": f"Last updated: {now}"},
        }

    fields = []
    total_dl = 0.0
    total_ul = 0.0

    for t in sorted(torrents, key=lambda x: x.get("added_on", 0), reverse=True):
        state  = t.get("state", "unknown")
        emoji  = STATE_EMOJI.get(state, "❓")
        prog   = t.get("progress", 0) * 100
        dl_spd = t.get("dlspeed", 0)
        ul_spd = t.get("upspeed", 0)
        eta    = t.get("eta", -1)
        size   = t.get("size", 0)
        name   = t.get("name", "Unknown")[:50]  # cap length

        total_dl += dl_spd
        total_ul += ul_spd

        # Build a compact one-liner value
        if state in ("uploading", "stalledUP", "forcedUP"):
            value = f"{emoji} `{state}` — {fmt_bytes(size)} | ⬆️ {fmt_speed(ul_spd)}"
        elif prog >= 100:
            value = f"✅ `complete` — {fmt_bytes(size)}"
        else:
            bar   = build_bar(prog)
            value = (
                f"{emoji} `{state}` {bar} **{prog:.1f}%**\n"
                f"⬇️ {fmt_speed(dl_spd)}  ETA: {fmt_eta(eta)}  Size: {fmt_bytes(size)}"
            )

        fields.append({"name": name, "value": value, "inline": False})

    # Discord embeds max 25 fields
    if len(fields) > 25:
        overflow = len(fields) - 24
        fields   = fields[:24]
        fields.append({"name": f"… and {overflow} more", "value": "​", "inline": False})

    description = (
        f"**Torrents:** {len(torrents)}  |  "
        f"**Total ⬇️** {fmt_speed(total_dl)}  |  "
        f"**Total ⬆️** {fmt_speed(total_ul)}"
    )

    return {
        "title": "📡 qBittorrent — Download Queue",
        "description": description,
        "color": 0x2ECC71,
        "fields": fields,
        "footer": {"text": f"Last updated: {now}"},
    }


def build_bar(pct: float, width: int = 10) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def build_event_embed(torrent: dict, event: str) -> dict:
    name = torrent.get("name", "Unknown")
    size = fmt_bytes(torrent.get("size", 0))
    if event == "added":
        return {
            "title": "➕ New torrent added",
            "description": f"**{name}**\nSize: {size}",
            "color": 0x3498DB,
        }
    if event == "completed":
        return {
            "title": "✅ Download complete",
            "description": f"**{name}**\nSize: {size}",
            "color": 0x2ECC71,
        }
    return {}


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def discord_post(embeds: list[dict]) -> str | None:
    """POST a new message to Discord.  Returns the message ID."""
    if not DISCORD_WEBHOOK:
        log.error("DISCORD_WEBHOOK is not set.")
        return None
    try:
        resp = requests.post(
            DISCORD_WEBHOOK,
            json={"embeds": embeds},
            params={"wait": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except requests.RequestException as exc:
        log.error("Discord POST failed: %s", exc)
        return None


def discord_edit(message_id: str, embeds: list[dict]) -> bool:
    """PATCH an existing Discord message."""
    if not DISCORD_WEBHOOK:
        return False
    try:
        resp = requests.patch(
            f"{DISCORD_WEBHOOK}/messages/{message_id}",
            json={"embeds": embeds},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Discord PATCH failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global known_torrents, summary_message_id

    if not DISCORD_WEBHOOK:
        log.error("DISCORD_WEBHOOK environment variable is not set. Exiting.")
        raise SystemExit(1)

    log.info("Starting qBittorrent Discord bot (poll interval: %ds)", POLL_INTERVAL)

    if not qbit_login():
        log.error("Initial login failed. Check QBIT_HOST, QBIT_USER, QBIT_PASS.")
        raise SystemExit(1)

    while True:
        torrents = qbit_get_torrents()
        current_hashes = {t["hash"] for t in torrents}
        torrent_map    = {t["hash"]: t for t in torrents}

        event_embeds: list[dict] = []

        # Detect new torrents
        for h, t in torrent_map.items():
            if h not in known_torrents:
                log.info("New torrent detected: %s", t.get("name"))
                event_embeds.append(build_event_embed(t, "added"))

        # Detect completed torrents (transition to completed/uploading from a non-complete state)
        complete_states = {"uploading", "stalledUP", "forcedUP", "pausedUP", "queuedUP"}
        for h, prev in known_torrents.items():
            if h in torrent_map:
                cur = torrent_map[h]
                was_done = prev.get("state") in complete_states or prev.get("progress", 0) >= 1.0
                is_done  = cur.get("state") in complete_states or cur.get("progress", 0) >= 1.0
                if is_done and not was_done:
                    log.info("Torrent completed: %s", cur.get("name"))
                    event_embeds.append(build_event_embed(cur, "completed"))

        # Post event notifications (adds / completions)
        if event_embeds:
            discord_post(event_embeds)

        # Update (or create) the persistent summary embed
        summary_embed = build_summary_embed(torrents)
        if summary_message_id:
            success = discord_edit(summary_message_id, [summary_embed])
            if not success:
                # Message may have been deleted — create a new one
                log.warning("Edit failed, creating a new summary message.")
                summary_message_id = discord_post([summary_embed])
        else:
            summary_message_id = discord_post([summary_embed])
            if summary_message_id:
                log.info("Created summary message ID: %s", summary_message_id)

        known_torrents = torrent_map
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
