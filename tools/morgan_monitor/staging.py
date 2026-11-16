"""Render an uninstalled collector unit; never install, start, or read auth.

The caller saves the returned text outside systemd's unit search paths. Rendering
does not establish source qualification or authorize deployment/ingestion.
"""
from pathlib import Path
import re


class StagingError(ValueError):
    pass


def _path(value: str) -> str:
    # Systemd expands percent specifiers and dollar variables. Keep paths in a
    # deliberately narrow literal subset rather than quoting untrusted syntax.
    if (not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            or len(value) > 4096 or any(part in (".", "..") for part in value.split("/"))):
        raise StagingError("invalid_staging_path")
    return value


def render_staged_service(checkout: str, config: str, environment_file: str) -> str:
    """Return a unit referencing approved paths, without opening those paths.

    The environment file is a reference only. Credential values are never read,
    returned, or copied. Installing/enabling the artifact is a separate action.
    """
    checkout, config, environment_file = map(_path, (checkout, config, environment_file))
    template = Path(__file__).with_name("morgan-monitor.service.example").read_text(encoding="utf-8")
    if template.count("/REPLACE/WITH/APPROVED/UIF/CHECKOUT") != 1 or template.count("/REPLACE/WITH/PRIVATE/CONFIG.json") != 1:
        raise StagingError("unit_template_drift")
    unit = template.replace("/REPLACE/WITH/APPROVED/UIF/CHECKOUT", checkout).replace("/REPLACE/WITH/PRIVATE/CONFIG.json", config)
    unit = unit.replace("# TEMPLATE ONLY: not installed, enabled, or started. Replace both placeholders.",
                        "# STAGED ONLY: uninstalled, not enabled, and not started by this renderer.")
    unit = unit.replace("# No token, EnvironmentFile, credential creation, source mutation, or TV change.\n# Provide approved source token environment variables separately if authorized.",
                        "# Existing approved credential file reference only; no values copied.\nEnvironmentFile=" + environment_file)
    if "/REPLACE/" in unit or unit.count("EnvironmentFile=") != 1:
        raise StagingError("unit_template_drift")
    return unit
