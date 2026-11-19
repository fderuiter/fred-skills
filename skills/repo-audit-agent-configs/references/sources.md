# Research references

Reviewed 2026-11-19. Use these as methods, not evidence about an unaudited repository.
Check the actual agent host version, skill loader behavior, and MCP transport semantics before reuse.
These files contain original instructions; third-party implementations are not bundled.

- [Agent Skills specification](https://agentskills.io/specification): Defines portable `SKILL.md`, `references/`, `assets/`, and executable `scripts/` package layout across hosts.
- [skills.sh documentation](https://skills.sh/docs): Documents open community skill distribution where download counts do not guarantee static vetting or sandboxing.
- [Model Context Protocol specification](https://modelcontextprotocol.io/specification): Defines stdio and HTTP/SSE server transports, tool schemas, and host trust responsibilities.
- [Unicode TR36 Security Considerations](https://www.unicode.org/reports/tr36/): Details bidirectional override spoofing, invisible characters, and confusable code point attacks in source and text files.
- [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/): Covers indirect prompt injection, excessive agency, and supply-chain risks in agent extensions and plugins.
