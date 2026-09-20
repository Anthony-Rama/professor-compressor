<div align="center">
  <img src="static/professor-compressor.png" alt="Professor Compressor mascot" width="180">

  # Professor Compressor

  A privacy-focused Discord bot that compresses videos in the user's browser and sends the finished files back to Discord.

  [Invite Professor Compressor](https://discord.com/oauth2/authorize?client_id=1550752400271351839&permissions=277025426432&integration_type=0&scope=bot+applications.commands)
</div>

## What it does

Run `/compress` in a Discord server, open the private link, and choose up to 10 videos. Professor Compressor automatically formats the results to fit the upload limit Discord reports for that server.

- Compression happens in the browser with FFmpeg WebAssembly.
- Original videos stay on the user's device.
- Compressed results are held briefly in server memory and relayed to Discord.
- Files are never written to disk by Professor Compressor.
- HTTPS is handled automatically by Caddy in the included Docker setup.
- Upload links are random, single-use, and expire automatically.

Professor Compressor currently accepts video files and produces Discord-compatible MP4 files. It does not compress images, GIFs, PDFs, or other document types.

## How it works

1. A member runs `/compress` in a server channel.
2. The bot creates a private, single-use browser link.
3. The browser loads the self-hosted FFmpeg WebAssembly encoder.
4. Videos are processed one at a time at up to 720p and 30 FPS.
5. Finished MP4 files are sent to the relay over HTTPS.
6. The relay posts the files to the original Discord channel and discards them from memory.

The Discord bot token is never exposed to the browser. Discord receives and stores the final attachments so server members can view them.

## Invite the hosted bot

Use the [official invite link](https://discord.com/oauth2/authorize?client_id=1550752400271351839&permissions=277025426432&integration_type=0&scope=bot+applications.commands). You must have permission to add apps to the destination server.

The bot requests only the channel permissions it needs:

- View Channels
- Send Messages
- Send Messages in Threads
- Attach Files
- Use Application Commands

## Run your own instance

### Requirements

- Python 3.11 or newer
- Node.js 22 and npm
- A Discord application and bot token
- A modern desktop browser

### Discord application setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open the **Bot** page and create or reset the bot token.
3. Keep the token private. Never place it in source code or commit it to Git.
4. Under **OAuth2**, select the `bot` and `applications.commands` scopes.
5. Select the permissions listed above and use the generated URL to install the app to a server.

No privileged gateway intents are required.

### Local development

```bash
git clone https://github.com/Anthony-Rama/discord-bot.git
cd professor-compressor

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

npm ci
cp .env.example .env
```

Set `DISCORD_TOKEN` in `.env`, then start the bot:

```bash
python bot.py
```

For local testing, keep these defaults:

```dotenv
WEB_HOST=127.0.0.1
WEB_PORT=8080
PUBLIC_BASE_URL=http://127.0.0.1:8080
```

The private link will only open on the same computer when those local settings are used.

### Docker deployment

The included Compose configuration runs the bot behind Caddy with automatic HTTPS.

1. Point a domain's DNS record to your server.
2. Allow inbound TCP traffic on ports 80 and 443.
3. Install Docker Engine and Docker Compose.
4. Copy `.env.example` to `.env` and configure at least `DISCORD_TOKEN` and `DOMAIN`.
5. Start the services.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f bot
```

Check the service health at `https://your-domain.example/healthz`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Required | Secret token from the Discord Developer Portal |
| `DOMAIN` | `compressor.example.com` | Public DNS name used by Caddy |
| `WEB_HOST` | `127.0.0.1` | Address used by the Python web service |
| `WEB_PORT` | `8080` | Port used by the Python web service |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8080` | Base URL placed in private upload links |
| `ALLOWED_GUILD_IDS` | Empty | Optional comma-separated allowlist of Discord server IDs |
| `JOB_TTL_MINUTES` | `30` | Lifetime of a private upload link |
| `USER_COOLDOWN_SECONDS` | `15` | Delay before one user can create another link |
| `MAX_ACTIVE_JOBS` | `250` | Maximum number of active upload links |
| `MAX_RESULT_TOTAL_MIB` | `220` | Maximum combined in-memory result size per request |

When `ALLOWED_GUILD_IDS` is empty, commands are available in every server that installs the bot. Set one or more server IDs to run a private instance:

```dotenv
ALLOWED_GUILD_IDS=123456789012345678,987654321098765432
```

## Privacy and security

- Never commit `.env`. It is ignored by both Git and Docker builds.
- Reset the Discord bot token immediately if it is exposed.
- Original videos are processed locally in the browser.
- Finished videos travel through the relay in memory because the bot must attach them to Discord.
- Upload links contain high-entropy tokens, expire, and can be used only once.
- Security headers enable cross-origin isolation for multithreaded browser encoding and block framing, camera, microphone, location, and payment access.

This design does not provide end-to-end encryption. The operator of a modified deployment and Discord can access the finished files. Review the code and host your own instance if that trust boundary does not meet your needs.

## Performance and limits

Browser FFmpeg is slower than native FFmpeg. Compression speed depends mainly on the user's computer, browser, video duration, and source resolution. The page must stay open and active until delivery finishes.

The practical input limit is the memory available to the browser tab. Videos are processed sequentially to reduce memory pressure. Very large files, older computers, and mobile browsers may fail or take a long time.

The included single-process relay is suitable for personal use and small communities. A large public deployment should add a durable queue, shared state, rate limiting, monitoring, and multiple workers before advertising high concurrency.

## Updating

```bash
git pull
docker compose up --build -d
docker compose ps
```

## Project structure

```text
app.py          Discord commands, upload relay, and HTTP server
web_ui.py       Browser interface and FFmpeg compression workflow
bot.py          Application entry point
compose.yaml    Bot and Caddy services
Caddyfile       HTTPS reverse proxy configuration
Dockerfile      Reproducible production image
static/         Branding assets
```

## License

No license has been granted yet. The source is public for inspection and personal evaluation. Add an explicit license before accepting outside contributions or redistributing modified versions.
