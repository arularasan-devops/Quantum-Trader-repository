# Quantum Trader Security

Never commit:

- API keys
- JWTs
- broker session tokens
- passwords
- cloud credentials
- private keys

Broker credentials must remain outside the AI-agent workspace.

The previously exposed broker credential must be rotated before broker connectivity is restored.

AI agents must not place broker orders.

Do not print secrets in reports or logs.

Use repository-scoped GitHub SSH access.

Use separate environment configuration for non-agent application processes.
