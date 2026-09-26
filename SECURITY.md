# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or include
tokens, private upload links, video files, or other sensitive data in a report.

Use GitHub's **Report a vulnerability** form in the repository's Security tab.
Include the affected version or commit, reproduction steps, expected impact,
and any suggested mitigation. Acknowledgement should arrive within seven days.

## Supported version

Only the latest commit on `main` is supported. Deployments made from forks or
modified builds are the responsibility of their operators.

## Security boundaries

- Original videos are encoded locally in the browser.
- Completed MP4 files pass through the relay in memory for Discord delivery.
- The service does not intentionally persist video files to disk.
- Discord stores delivered attachments under its own policies.
- A self-hosted operator can inspect relay memory and traffic; this is not an
  end-to-end encrypted service.

Never send real credentials with a report. Rotate a credential immediately if
it may have been exposed.
