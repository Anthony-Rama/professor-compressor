# Development

This is the maintainer's development and test workflow. Bug reports and feature
suggestions are welcome, but outside code contributions are not currently
accepted. See the README for the original code's reuse terms.

## Maintainer development workflow

1. Create a focused branch from `main`.
2. Create a private `.env` using the configuration below and a separate development Discord application.
3. Install Python and browser dependencies.
4. Add or update tests for behavior changes.
5. Run the complete local quality suite before opening a pull request.

```bash
python -m pip install -r requirements-dev.txt
npm ci
python -m ruff check .
python -m ruff format --check .
python -m pip_audit --requirement requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q bot.py professor_compressor tests
npm audit --omit=dev
npx playwright install chromium
npm run test:browser
docker build --tag professor-compressor:local .
```

Browser tests also require `ffmpeg` on PATH and simulate Discord delivery.

Keep pull requests small enough to review. Explain the user-visible behavior,
the security or privacy impact, and how the change was verified.

## Repository rules

- Never commit `.env`, credentials, webhook URLs, private keys, production IP
  addresses, user content, or private upload links.
- Do not add telemetry containing user, server, channel, file, or IP data.
- Keep browser compression local. Only completed output files may cross the
  relay boundary.
- Pin production dependencies and explain new third-party services.
- Treat filenames, HTTP headers, Discord names, and uploaded bytes as untrusted.

Security reports must be sent privately, not in public issues. See
[SECURITY.md](SECURITY.md) for the email address and reporting instructions.

## Private development configuration

Create `.env` locally; it is ignored by Git and Docker's build context. Set
`DISCORD_TOKEN` to the separate development bot's token and `PUBLIC_BASE_URL`
to the address that your test browser can reach (for a local desktop test,
`http://127.0.0.1:8080`). Run `python bot.py`. Do not run a second instance using
the production bot token. Install the development bot with View Channel, Send
Messages, and Attach Files; thread tests also need Send Messages in Threads.

The browser tests do not need credentials and never connect to Discord.
On a Mac with Chrome already installed, set `CHROME_EXECUTABLE` to the Chrome
executable and `TEST_PYTHON` to the Python environment used for the test server.

The normal bot-message size target excludes the invoking user's Nitro allowance.
`BOT_BASE_UPLOAD_MIB` defaults to 20, following the current
[Discord upload reference](https://github.com/discord/discord-api-docs/blob/main/developers/reference.mdx#uploading-files).
Guild boost allowances can raise this limit. The interaction allowance is used
only as an additional upper bound. The pinned discord.py release still carries
an older base allowance, so review this override whenever Discord changes its
limits; do not increase it just to accept larger files.

## Readiness and releases

- `/healthz` is HTTP liveness and aggregate load; it also includes `revision`.
- `/readyz` returns 200 only when Discord is connected, commands are synced,
  every configured delivery worker is alive, and the process is not draining.
  Otherwise it returns 503. Docker checks this endpoint. Configure external
  monitoring against `/readyz`; an unhealthy Docker container is not automatically
  restarted by `restart: unless-stopped`.
- Set `APP_REVISION` to the release commit when building the image, for example
  `APP_REVISION="$(git rev-parse HEAD)" docker compose build bot`.
- SIGTERM/SIGINT stop new sessions and allow active work up to 120 seconds to
  finish. Compose allows 150 seconds before force-stopping the process. Do not
  use `docker kill` or shorten that grace period for a routine release.
- Sessions and delivery receipts are in memory. A restart still invalidates
  idle links and can interrupt work longer than the grace period. Deploy at a
  quiet time and check both readiness and the release revision afterward.
- Browser package URLs include a version fingerprint and are immutable. Legacy
  unversioned paths remain available without long-lived cache headers. Change
  package versions when updating assets; do not modify installed npm distributions.

## Live acceptance checks

Use a dedicated development server, not a user's channel. Check non-Nitro and
Nitro accounts in an unboosted server, a boosted server, a permission-denied
channel, normal and private threads, and a permission change after creating a
link. Verify the actual attachment and audio playback, not only the success
alert. Also check Firefox/Safari, portrait and variable-frame-rate recordings,
and large media on a memory-constrained device. Automated tests simulate Discord
delivery and do not establish these external-platform results.
