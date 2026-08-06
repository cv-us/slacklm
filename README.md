# SlackLM

A Slack bot that answers questions by querying Google NotebookLM notebooks. Users @mention the bot in Slack channels, and it retrieves answers from PDF sources uploaded to NotebookLM.

## How it works

```
User @mentions bot in Slack
        ↓
  Slack Bolt (Socket Mode)
        ↓
    Query Router
        ↓
  ┌─────────────────────────┐
  │ Primary: NotebookLM     │  ← Uses notebooklm-py to query the notebook directly
  │ Fallback: Claude API    │  ← Reads notebook sources via tool_use, reasons over them
  └─────────────────────────┘
        ↓
  Formatted answer with citations
        ↓
  Reply in Slack thread
```

- **Primary engine**: Queries your NotebookLM notebook directly using the unofficial `notebooklm-py` library
- **Fallback engine**: When NotebookLM is unavailable, Claude API reads the notebook's source documents via tool_use and generates answers
- **Channel mapping**: Each Slack channel maps to a specific NotebookLM notebook via `config/channels.yaml`

## Usage in Slack

| Command | Description |
|---------|-------------|
| `@SlackLM what is our PTO policy?` | Ask a question using this channel's default notebook |
| `@SlackLM in engineering-docs: how do we deploy?` | Ask using a specific notebook (by name) |
| `@SlackLM sources` | List all sources in this channel's notebook |
| `@SlackLM help` | Show usage instructions |

The bot also responds to direct messages.

**Thread follow-ups (optional, off by default):** with `auto_thread_replies: true` in `config/channels.yaml`, anyone can keep asking questions in a thread the bot has answered without @mentioning it again. Left off, every question requires an explicit @mention so casual thread chatter never triggers the bot. (Thread memory is per-process: after a bot restart, older threads need one fresh @mention.) Enabling this also requires the `message.channels` event and `channels:history` scope on the Slack app.

**Reply style:** by default the bot replies in a thread under the user's message. Set `reply_style: channel` in `config/channels.yaml` to have it post answers as regular top-level channel messages instead — replies threaded onto the bot's message are then treated as follow-ups.

**Idle keepalive:** the bot pings NotebookLM every 10 minutes to keep the Google session cookies rotating even when nobody is asking questions. Google's session freshness token (`__Secure-1PSIDTS`) must be re-rotated on a ~10-minute cadence — a session left unrotated for a few hours is invalidated server-side, so this cadence is what keeps the stored login alive for weeks/months. If rotation starts failing repeatedly, the bot can post a warning to a configured `admin_channel` so you can re-authenticate before it goes fully dark.

---

## Setup Guide

### Prerequisites

- Python 3.11+
- A Google account with access to [NotebookLM](https://notebooklm.google.com)
- A Slack workspace where you can install apps
- An [Anthropic API key](https://console.anthropic.com) (for Claude fallback)

### Step 1: Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it (e.g., "SlackLM") and select your workspace

3. **Enable Socket Mode:**
   - Left sidebar → Settings → **Socket Mode** → Toggle ON
   - Generate an app-level token with `connections:write` scope
   - Save this token as `SLACK_APP_TOKEN` (starts with `xapp-`)

4. **Subscribe to Events:**
   - Left sidebar → Features → **Event Subscriptions** → Toggle ON
   - Under "Subscribe to bot events", add:
     - `app_mention`
     - `message.im`
     - `message.channels` (only if you enable `auto_thread_replies`)
     - `message.groups` (same, for private channels — optional)

5. **Set Bot Permissions:**
   - Left sidebar → Features → **OAuth & Permissions**
   - Under "Bot Token Scopes", add:
     - `app_mentions:read`
     - `chat:write`
     - `im:history`
     - `im:read`
     - `im:write`
     - `channels:history` (only if you enable `auto_thread_replies`)
     - `groups:history` (same, for private channels — optional)

6. **Install the App:**
   - Left sidebar → Settings → **Install App** → **Install to Workspace**
   - Authorize the app
   - Save the **Bot User OAuth Token** as `SLACK_BOT_TOKEN` (starts with `xoxb-`)

7. **Invite the bot** to your channels:
   ```
   /invite @SlackLM
   ```

### Step 2: Authenticate NotebookLM (master-token auth — required for servers)

The bot uses `notebooklm-py` to query your notebooks. **Use master-token auth**: plain cookie logins minted on your PC get killed by Google within hours when replayed from a datacenter IP, whereas a master token lets the server mint (and automatically re-mint) its own sessions locally — self-healing, unattended.

One-time bootstrap on your local machine:

```bash
pip install --upgrade "notebooklm-py[browser,headless]"
playwright install chromium
notebooklm login --master-token --account your-email@gmail.com
```

A browser window opens at Google's sign-in — log in and wait for verification ("N notebooks"). This writes **two files** into your notebooklm profile dir (`~/.notebooklm/profiles/default/` or `%USERPROFILE%\.notebooklm\profiles\default\`):

- `master_token.json` — durable credential the server uses to mint fresh sessions
- `storage_state.json` — the current cookie session

Copy **both** to the server. With `master_token.json` present, the client auto-recovers dead sessions with no human involved (the library's layer-4 recovery).

> **Security note:** `master_token.json` is a durable, full-account credential. Strongly consider a **dedicated Google account** for the bot (share your notebooks with it) rather than your personal account. Delete local copies after transferring to the server.

> If Google blocks the automated browser ("This browser or app may not be secure"), launch your own Chrome with `--remote-debugging-port=9222` and retry with `--cdp-url http://localhost:9222`.

### Step 3: Get Your Notebook ID

Your NotebookLM notebook URL looks like:
```
https://notebooklm.google.com/notebook/1395e9ba-90ba-45b0-a435-761f0e4ce313
```

The notebook ID is the UUID at the end: `1395e9ba-90ba-45b0-a435-761f0e4ce313`

### Step 4: Get an Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com) → **API Keys** → **Create Key**
2. Save as `ANTHROPIC_API_KEY`

### Step 5: Configure

1. Copy the example env file and fill in your tokens:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env`:
   ```
   SLACK_BOT_TOKEN=xoxb-your-bot-token
   SLACK_APP_TOKEN=xapp-your-app-token
   ANTHROPIC_API_KEY=sk-ant-your-key
   ```

2. Create your channel config from the example, then map your Slack channels to notebooks:
   ```bash
   cp config/channels.example.yaml config/channels.yaml
   ```
   `config/channels.yaml` is gitignored — it's your live config and never conflicts with `git pull`.

   Edit it:
   ```yaml
   channels:
     C0123456789:
       notebook_id: "1395e9ba-90ba-45b0-a435-761f0e4ce313"
       name: "My Knowledge Base"
     default:
       notebook_id: "1395e9ba-90ba-45b0-a435-761f0e4ce313"
       name: "Default"
   
   claude_fallback:
     enabled: true
     model: "claude-sonnet-4-6"
   ```

   **Finding your Slack channel ID:** Right-click the channel name → **View channel details** → scroll to the bottom.

### Step 6: Run Locally

```bash
# Install dependencies
pip install -e ".[dev]"

# Make sure storage_state.json is in the project root
cp /path/to/storage_state.json .

# Start the bot
python -m app.main
```

Test in Slack:
- `@SlackLM help` — should show usage
- `@SlackLM sources` — should list notebook sources
- `@SlackLM what are the key topics?` — should reply with an answer and citations

### Step 7: Run Tests

```bash
pytest
```

---

## Deployment (Oracle Cloud Free Tier)

Oracle Cloud offers a forever-free ARM VM (4 CPU, 24GB RAM) — more than enough for this bot.

### Create the VM

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (Free Tier — requires a credit card but won't charge)
2. **Compute** → **Instances** → **Create Instance**
   - Shape: **Ampere A1** (ARM) — 2 OCPUs, 12GB RAM
   - Image: **Ubuntu 22.04** Minimal
   - Add your SSH public key
3. Note the public IP address

### Set Up the VM

```bash
# SSH into the VM
ssh ubuntu@<your-vm-public-ip>

# Run the setup script
bash deploy/setup-oracle.sh

# Log out and back in (for docker group permissions)
exit
ssh ubuntu@<your-vm-public-ip>
```

Or install manually:
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker
sudo usermod -aG docker $USER
```

### Deploy

```bash
# Clone the repo on the VM
git clone https://github.com/cv-us/slacklm.git
cd slacklm
```

From your **local machine**, copy config files to the VM:
```bash
scp .env ubuntu@<vm-ip>:~/slacklm/.env
# Auth files go inside the notebooklm-profile/ directory (mounted into the
# container so cookie rotation and master-token re-mints persist to disk)
ssh ubuntu@<vm-ip> "mkdir -p ~/slacklm/notebooklm-profile"
scp storage_state.json ubuntu@<vm-ip>:~/slacklm/notebooklm-profile/storage_state.json
scp master_token.json ubuntu@<vm-ip>:~/slacklm/notebooklm-profile/master_token.json
scp config/channels.yaml ubuntu@<vm-ip>:~/slacklm/config/channels.yaml
```

Back on the VM:
```bash
# Build and start
docker compose up -d --build

# Check logs
docker compose logs -f

# Verify it's running
docker compose ps
```

The bot will auto-restart on reboot (Docker `restart: always` policy).

### Verify

| Test | Command | Expected |
|------|---------|----------|
| Bot online | Check Slack | Green dot next to bot name |
| Basic question | `@SlackLM what is...?` | Threaded reply with citations |
| DM | Direct message the bot | Reply with answer |
| Sources | `@SlackLM sources` | Lists notebook sources |
| Fallback | Temporarily rename `notebooklm-profile/storage_state.json`, ask a question | Claude answers (may be slower) |
| Logs | `docker compose logs -f` | No errors |

---

## Maintenance

### Session hygiene (important)

The bot's Google session lives in `notebooklm-profile/storage_state.json` on the server. Google invalidates a session if it sees stale copies of its cookies being replayed, so exactly **one** machine may use a given cookie set:

- After copying `storage_state.json` to the server, **delete the local copy** (`C:\Users\<you>\.notebooklm\profiles\default\storage_state.json`).
- **Don't run `notebooklm login` or the notebooklm CLI locally while the bot is live** — only when you intend to replace the server's session (login → scp → restart).
- For extra robustness, consider a dedicated Google account for the bot.

| Task | How |
|------|-----|
| **NotebookLM auth expired** | With `master_token.json` on the server this self-heals automatically — dead sessions re-mint from the master token with no action needed. Manual re-auth is only needed if the master token itself is revoked (password change, security event): re-run `notebooklm login --master-token --account <email>` locally, then on the VM `rm -f ~/slacklm/notebooklm-profile/*.json` (container writes as root), scp both files back up, `docker compose restart`, and delete local copies |
| **Add documents** | Upload PDFs in NotebookLM web UI — no bot restart needed |
| **Add/change channel mapping** | Edit `config/channels.yaml` on VM, `docker compose restart` |
| **Update bot code** | `git pull && docker compose up -d --build` |
| **View logs** | `docker compose logs -f` |
| **Stop the bot** | `docker compose down` |

---

## Project Structure

```
slacklm/
├── app/
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration loading
│   ├── slack_handler.py     # Slack event handlers
│   ├── query_router.py      # Routes queries to NotebookLM or Claude
│   ├── notebooklm_client.py # NotebookLM API wrapper
│   ├── claude_client.py     # Claude fallback with tool_use
│   └── formatter.py         # Slack Block Kit message formatting
├── config/
│   └── channels.example.yaml  # Channel → notebook mapping template (copy to channels.yaml)
├── tests/                   # Unit tests
├── deploy/
│   └── setup-oracle.sh      # Oracle Cloud VM setup script
├── .env.example             # Environment variable template
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```
