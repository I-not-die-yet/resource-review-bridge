# Security Policy

## Supported version

Only the latest commit on the default branch is supported during the initial development phase.

## Report a vulnerability

Use GitHub's private vulnerability reporting for issues that could expose credentials, local files, private-network services, retained evidence, or unintended mutation capabilities. Do not include real secrets, private screenshots, or personal data in a public issue.

For ordinary bugs that do not involve sensitive data, open a normal GitHub issue with a minimal public reproduction.

## Operational boundary

Resource Review Bridge is intended for public, read-only web acquisition. It must not be configured to:

- log in to user accounts;
- bypass paywalls, CAPTCHAs, or access controls;
- submit forms or interact with social accounts;
- access localhost, private networks, or credential-bearing URLs;
- expose arbitrary shell, filesystem, or Codex RPC tools;
- commit runtime keys, Tunnel profiles, SQLite state, logs, or captured screenshots.

Run the bridge with the least privileges available. Treat retained evidence and screenshots as potentially sensitive even when the source page was public.

The bridge is designed for a local, single-user environment. Do not expose its stdio process as an unauthenticated network service or use it as a multi-tenant crawler without a separate security review. Page snapshots and screenshots are sent to the user's configured Codex service to generate the Evidence Packet.
