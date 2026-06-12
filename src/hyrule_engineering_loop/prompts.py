"""Prompt artifact loading for role-node hydration.

Phase C rebinds role prompts from the v1 ``docs/agent-loops/`` role cards to
the ``skills/`` tree (``skills/README.md``): each role's working contract is
its ``SKILL.md`` — trigger frontmatter, workflow checkpoints,
anti-rationalization table, and exit criteria. The v1 role cards remain in
``docs/agent-loops/`` as lineage until the Phase G extraction.
"""

from __future__ import annotations

from pathlib import Path

ROLE_PROMPT_FILES: dict[str, str] = {
    "network_architect": "role-network-architect/SKILL.md",
    "systems_engineer": "role-systems-engineer/SKILL.md",
    "devops_netops": "role-devops-netops/SKILL.md",
    "security_auditor": "role-security-auditor/SKILL.md",
    "finops_integrity": "role-finops-integrity/SKILL.md",
    "virtual_lab_chaos": "role-virtual-lab-chaos/SKILL.md",
    "implementation_writer": "implementation-tranche/SKILL.md",
}


def default_prompt_dir() -> Path:
    """Return the repo-local skills directory."""
    return Path(__file__).resolve().parents[2] / "skills"


def load_role_prompts(prompt_dir: Path | None = None) -> dict[str, str]:
    """Load role skill documents for model binding."""
    base = prompt_dir or default_prompt_dir()
    prompts: dict[str, str] = {}
    for role, filename in ROLE_PROMPT_FILES.items():
        prompts[role] = (base / filename).read_text(encoding="utf-8")
    return prompts
