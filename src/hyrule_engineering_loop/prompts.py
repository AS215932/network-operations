"""Prompt artifact loading for future role-node hydration."""

from __future__ import annotations

from pathlib import Path

ROLE_PROMPT_FILES: dict[str, str] = {
    "network_architect": "senior-network-architect.md",
    "systems_engineer": "senior-systems-engineer.md",
    "devops_netops": "senior-devops-netops-engineer.md",
    "security_auditor": "senior-security-cryptographic-auditor.md",
    "finops_integrity": "finops-billing-integrity-engineer.md",
    "virtual_lab_chaos": "virtual-lab-chaos-simulation-engineer.md",
}


def default_prompt_dir() -> Path:
    """Return the repo-local role prompt directory."""
    return Path(__file__).resolve().parents[2] / "docs" / "agent-loops"


def load_role_prompts(prompt_dir: Path | None = None) -> dict[str, str]:
    """Load Markdown role prompts for later model binding."""
    base = prompt_dir or default_prompt_dir()
    prompts: dict[str, str] = {}
    for role, filename in ROLE_PROMPT_FILES.items():
        prompts[role] = (base / filename).read_text(encoding="utf-8")
    return prompts
