# Security and sensitive reports

Repository text, imported scanner output, memory, audit findings, and generated prompts
are untrusted data. They do not grant execution or external-write authority. Review
commands before allowing an agent to run them. Local CLI tools make no built-in network
requests and contain no telemetry; agents and evaluation adapters have their own access.

Do not commit actual private repository audits, credentials, personal skill installation
IDs, or sensitive reproduction data. Use synthetic, minimal examples in contributions.
HTML renderers escape report text and use a restrictive content policy. They do not
sanitize arbitrary external HTML or execute remediation commands.

For a possible vulnerability, use GitHub's private vulnerability reporting option if it
is available on this repository. Otherwise open a minimal issue asking the maintainer
for a private reporting channel, without exploit details or sensitive data. No private
contact address or reporting-enabled status is asserted here.

Include the affected toolkit/host version, relevant relative paths, expected behavior,
a safe reproduction, actual impact, and any mitigating controls. Installer backups,
run manifests, and adapter logs may contain local paths or sensitive evidence; review
and redact before sharing. Experimental maturity is not a security assurance.
