# Security policy

## Reporting a vulnerability

Do not attach real transcripts, credentials, or private reports to a public issue. Share a minimal synthetic reproducer with the project maintainer through the private security-reporting channel configured on the eventual GitHub repository.

## Security model

- Runtime code is Python-standard-library only and makes no network calls.
- Hooks run with the current OS user's permissions and should be reviewed after every update in both hosts.
- Transcript content is untrusted evidence, never an instruction source.
- Private paths, symlinks/junctions, capture identity, size, and SHA-256 are validated before evidence is accepted.
- Report redaction is defense in depth; users must review reports before sharing.

- Plugin runtime code does not use the network; marketplace fetch/update behavior belongs to the host.

Supported releases receive fixes on the latest minor version. Pin a reviewed commit or release when deploying to managed environments.
