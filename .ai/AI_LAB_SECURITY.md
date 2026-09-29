# Quantum Trader AI Lab Security

AI agents must not receive:

- broker API keys
- broker passwords
- JWTs
- session tokens
- cloud credentials
- private keys

The previously exposed broker credential must be rotated before broker connectivity is restored.

Never place credentials in:

- source files
- Git
- agent prompts
- reports
- logs
- ZIP overlays

Live broker order execution must remain outside the AI-agent workspace.
