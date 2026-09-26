# Contributing

Thanks for improving Professor Compressor.

## Development workflow

1. Create a focused branch from `main`.
2. Create a private `.env` using the README setup instructions and a separate development Discord application.
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

Security reports belong in GitHub's private vulnerability-reporting form, not
in public issues. See [SECURITY.md](SECURITY.md).
