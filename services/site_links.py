from __future__ import annotations

import os


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def get_site_links() -> dict[str, str]:
    ecosystem_url = os.getenv("AIHA_ECOSYSTEM_URL", "/")
    studio_url = os.getenv("AIHA_STUDIO_URL", "/studio")
    consulting_url = os.getenv("AIHA_CONSULTING_URL", "/consulting")

    return {
        "ecosystem_url": ecosystem_url,
        "studio_url": studio_url,
        "consulting_url": consulting_url,
        "consulting_audit_url": _join_url(consulting_url, "audit"),
        "consulting_diagnostic_url": _join_url(consulting_url, "diagnostic"),
    }