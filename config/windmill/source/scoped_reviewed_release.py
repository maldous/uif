"""Scoped Windmill action and private Foo handler contract.

The install adapter and reviewed pins must be bound in deployed composition,
never by the public action's caller. Do not mount the handler on generic root
SSH or a public route. No second identity provider is involved.
"""

import os
import re
from typing import NamedTuple
from uuid import UUID

SELECTORS = ("project", "repository")
FIELDS = ("operation_id", "release_sha256", "package_sha256",
          "protected_request_sha256")
PROFILE = "offmenu-native-upgrade-v1"
SHA256 = re.compile("[0-9a-f]{64}")


class Release(NamedTuple):
    operation_id: str
    release_sha256: str
    package_sha256: str
    protected_request_sha256: str


def run_id(value):
    if type(value) is not str or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError("INVALID_WINDMILL_RUN_ID")
    return value


def exact_release(request):
    if type(request) is not dict or request.keys() != set(FIELDS):
        raise ValueError("INVALID_RELEASE_FIELDS")
    values = tuple(request[key] for key in FIELDS)
    if any(type(value) is not str or not SHA256.fullmatch(value) for value in values):
        raise ValueError("INVALID_RELEASE_PIN")
    return Release(*values)


class ProjectRoute(NamedTuple):
    project: str
    repository: str
    baseline: str
    state: str
    profile: str | None


def build_registry(canonical, policy):
    """Use only independently reviewed deployment configuration, never request JSON."""
    if (canonical.get("schema") != "uif-environment-profiles-v1"
            or policy.get("schema") != "uif-windmill-release-projects-v1"
            or policy.get("canonical_roles") != "docs/agents/environment-profiles.json"):
        raise ValueError("INVALID_RELEASE_REGISTRY")
    roles, projects = canonical["logical_roles"], policy["projects"]
    if set(roles) != set(projects):
        raise ValueError("INCOMPLETE_RELEASE_REGISTRY")
    registry = {}
    for project, role in roles.items():
        state = projects[project]["state"]
        profile = projects[project].get("profile")
        repositories = role["repository_binding"]
        if (state not in {"eligible", "held", "infrastructure-only", "unqualified"}
                or (state == "eligible") != bool(profile)
                or not repositories or len(repositories) != len(set(repositories))):
            raise ValueError("INVALID_RELEASE_PROJECT")
        for repository in repositories:
            registry[project, repository] = ProjectRoute(
                project, repository, role["baseline"], state, profile)
    return registry


def resolve(registry, project, repository):
    if type(project) is not str or type(repository) is not str:
        raise ValueError("INVALID_RELEASE_PROJECT_SELECTOR")
    try:
        return registry[project, repository]
    except KeyError:
        raise ValueError("UNKNOWN_RELEASE_PROJECT_SURFACE") from None


def _selected(request, registry):
    if type(request) is not dict or request.keys() != set(SELECTORS + FIELDS):
        raise ValueError("INVALID_RELEASE_FIELDS")
    route = resolve(registry, *(request[key] for key in SELECTORS))
    if route.state != "eligible":
        raise PermissionError("RELEASE_PROJECT_NOT_ELIGIBLE")
    return route, exact_release({key: request[key] for key in FIELDS})


def bind_project_action(registry, send_to_private_foo):
    """One deployed action; project selection never injects an adapter or path."""
    def main(request):
        route, pins = _selected(request, registry)
        if os.environ.get("WM_WORKSPACE") != "foo":
            raise PermissionError("WRONG_WINDMILL_WORKSPACE")
        job = run_id(os.environ.get("WM_JOB_ID"))
        if send_to_private_foo is None:
            raise RuntimeError("SCOPED_FOO_CHANNEL_UNBOUND")
        return send_to_private_foo(route.profile, job, dict(
            project=route.project, repository=route.repository, **pins._asdict()))
    return main


def bind_project_receiver(registry, protected_handlers):
    """Resolve again at Foo; handlers are fixed trusted deployment composition."""
    def receive(profile, job, request):
        route, pins = _selected(request, registry)
        run_id(job)
        if profile != route.profile:
            raise ValueError("WRONG_RELEASE_PROFILE")
        handler = protected_handlers.get((route.project, route.repository, route.profile))
        if handler is None:
            raise RuntimeError("PROTECTED_RELEASE_ADAPTER_UNBOUND")
        return handler(profile, job, pins._asdict())
    return receive


def bind_foo_handler(approved, run_original):
    """Bind only behind private ingress, using independently reviewed pins.

    run_original must be the fixed project adapter that durably consumes and
    reconciles the original operation. The Windmill run ID is audit context,
    not the dedupe key, and a new Windmill run never creates a new operation.
    """
    def receive(profile, job, request):
        if profile != PROFILE:
            raise ValueError("WRONG_RELEASE_PROFILE")
        run_id(job)
        pins = exact_release(request)
        if approved is None or run_original is None:
            raise RuntimeError("PROTECTED_RELEASE_ADAPTER_UNBOUND")
        if pins != approved:
            raise PermissionError("UNREVIEWED_RELEASE")
        return run_original(pins.operation_id, job, pins)
    return receive


# No verified restricted transport is installed. Keep the draft entrypoint shut.
def main(request):
    raise RuntimeError("SCOPED_FOO_CHANNEL_UNBOUND")
