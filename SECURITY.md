# Reporting a security issue

Contact the maintainer privately through the channel where you obtained the software. For a hosted installation, contact its operator first if customer data or credentials may be involved. If the repository offers **Security → Report a vulnerability**, use that private form. Keep sensitive reports out of public issues.

Include the affected revision, interface (HTTP, MCP, CLI or provider integration), role used, expected boundary and observed impact. Use a disposable organization and sanitized evidence. Leave customer reports, live tokens, CI signing keys, graph backups and private URLs out of the initial message.

Operators should use a separate graph, identity registry and lease store per organization. The local CLI runs with the privileges of its account. Give remote agents named worker credentials and expose the private console over authenticated HTTPS. The public demo is a separate, read-only process on a disposable graph. See the [deployment guide](https://ichnix.vercel.app/docs#install).
