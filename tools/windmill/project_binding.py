"""Pure, plan-only bindings for NEW operations on a shared Foo host service.

No execution, network, filesystem access, credentials, admission or receipts.
Registry/observation provenance must be established by their owning boundary;
valid dictionaries and hashes alone do not establish deployment or authorization.
Legacy operations remain with their original readers and immutable identities.
"""
import hashlib
import json
from pathlib import PurePosixPath
import re


REGISTRY = 'foo-project-registry/v1'
ENVELOPE = 'foo-project-operation/v1'
OBSERVATION = 'foo-project-source-observation/v1'
PLAN = 'foo-project-dispatch-plan/v1'
RESOURCE_FIELDS = {'workers', 'overhead_processes', 'cpu_millis', 'memory_bytes',
                   'output_bytes', 'io_slots'}
MAX_BYTES = 65536


class BindingError(ValueError):
    """A declaration cannot form a new immutable dispatch plan."""


def require(condition, reason):
    if not condition:
        raise BindingError(reason)


def fields(value, expected, reason):
    require(type(value) is dict and set(value) == set(expected), reason)


def canonical_bytes(value):
    """Bounded JSON with string keys, integer numbers, no coercion or floats."""
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        require(depth <= 16 and count <= 4096, 'json_complexity')
        if type(item) is dict:
            require(all(type(key) is str for key in item), 'json_key')
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            require(len(item) <= MAX_BYTES and not any(
                0xD800 <= ord(char) <= 0xDFFF for char in item), 'json_string')
        else:
            require(item is None or type(item) is bool or
                    type(item) is int and abs(item) <= 2**53 - 1, 'json_type')

    visit(value, 0)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode('ascii')
    require(len(raw) <= MAX_BYTES, 'json_size')
    return raw


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def detached(value):
    return json.loads(canonical_bytes(value))


def text(value, reason, limit=256):
    require(type(value) is str and 0 < len(value) <= limit and value.strip() == value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), reason)


def identifier(value, reason):
    require(type(value) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,79}', value), reason)


def pin(value, reason, width=64):
    require(type(value) is str and re.fullmatch('[0-9a-f]{' + str(width) + '}', value), reason)


def path(value):
    text(value, 'path', 4096)
    p = PurePosixPath(value)
    require(p.is_absolute() and value.startswith('/') and not value.startswith('//')
            and str(p) == value and '..' not in p.parts and '\\' not in value
            and value != '/', 'path_canonical')
    return p


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def within(child, parent):
    return child == parent or parent in child.parents


def path_list(value, reason, minimum=0):
    require(type(value) is list and minimum <= len(value) <= 32, reason)
    paths = [path(item) for item in value]
    require(len(set(paths)) == len(paths), reason)
    return paths


def resource_numbers(value, expected):
    fields(value, expected, 'resource_fields')
    for key, number in value.items():
        require(type(number) is int and (0 if key == 'output_bytes' else 1)
                <= number <= 2**53 - 1, 'resource_number')


def profile_digest(profile):
    """Qualification pins exactly the profile body, excluding its own evidence."""
    require(type(profile) is dict, 'profile')
    return digest({key: value for key, value in profile.items() if key != 'qualification'})


def validate_registry(value):
    registry = detached(value)
    fields(registry, {'schema', 'host_id', 'service', 'profiles'}, 'registry_fields')
    require(registry['schema'] == REGISTRY, 'registry_version')
    identifier(registry['host_id'], 'host_id')
    service = registry['service']
    fields(service, {'service_id', 'root', 'runner_sha256', 'policy_sha256',
                     'admission_authority', 'admission_root', 'resource_limits'}, 'service_fields')
    for key in ('service_id', 'admission_authority'):
        identifier(service[key], key)
    for key in ('runner_sha256', 'policy_sha256'):
        pin(service[key], key)
    resource_numbers(service['resource_limits'], RESOURCE_FIELDS | {'timeout_seconds'})
    roots = [path(service['root']), path(service['admission_root'])]
    require(not overlaps(*roots), 'service_admission_overlap')
    profiles = registry['profiles']
    require(type(profiles) is list and 1 <= len(profiles) <= 32, 'profiles')
    projects, names, repositories = set(), set(), set()
    for profile in profiles:
        fields(profile, {'project_id', 'profile_id', 'repository', 'owner', 'uid', 'gid',
                         'checkout_root', 'worktree_root', 'state_root', 'data_roots',
                         'executables', 'resource_limits', 'isolation_sha256',
                         'qualification'}, 'profile_fields')
        for key in ('project_id', 'profile_id', 'owner'):
            identifier(profile[key], key)
        require(type(profile['repository']) is str and re.fullmatch(
            r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', profile['repository']), 'repository')
        require(profile['repository'].split('/')[0] not in ('.', '..') and
                profile['repository'].split('/')[1] not in ('.', '..'), 'repository')
        require(profile['project_id'] not in projects and profile['profile_id'] not in names
                and profile['repository'].casefold() not in repositories, 'duplicate_project')
        projects.add(profile['project_id']); names.add(profile['profile_id'])
        repositories.add(profile['repository'].casefold())
        for key in ('uid', 'gid'):
            require(type(profile[key]) is int and 1 <= profile[key] <= 2**31 - 1,
                    'nonroot_execution_identity')
        pin(profile['isolation_sha256'], 'isolation_pin')
        project_roots = [path(profile[key]) for key in
                         ('checkout_root', 'worktree_root', 'state_root')]
        project_roots += path_list(profile['data_roots'], 'data_roots')
        for candidate in project_roots:
            require(not any(overlaps(candidate, other) for other in roots), 'root_overlap')
            roots.append(candidate)
        path_list(profile['executables'], 'executables', minimum=1)
        # Executables must belong to a separately qualified runtime, not mutable
        # project source/data/state or admission storage.
        for executable in map(path, profile['executables']):
            require(not any(within(executable, other) for other in roots[1:]),
                    'project_executable')
        limits = profile['resource_limits']
        resource_numbers(limits, RESOURCE_FIELDS | {'timeout_seconds'})
        require(all(limits[key] <= service['resource_limits'][key] for key in limits),
                'profile_exceeds_host_limits')
        qualified = profile['qualification']
        fields(qualified, {'status', 'profile_sha256', 'service_sha256', 'evidence_sha256'},
               'qualification_fields')
        require(qualified['status'] == 'deployment-qualified', 'profile_not_deployment_qualified')
        for key in ('profile_sha256', 'service_sha256', 'evidence_sha256'):
            pin(qualified[key], 'qualification_pin')
        require(qualified['profile_sha256'] == profile_digest(profile)
                and qualified['service_sha256'] == digest(service), 'qualification_drift')
    # Check again after every project is known: an earlier profile must not
    # select an executable from a later project's mutable root.
    for profile in profiles:
        for executable in map(path, profile['executables']):
            require(not any(within(executable, other) for other in roots[1:]),
                    'project_executable')
    return registry


def reviewed_binding_digest(envelope):
    require(type(envelope) is dict, 'envelope')
    return digest({key: value for key, value in envelope.items() if key != 'review'})


def plan_dispatch(envelope, registry, observation, *, existing_operation=None):
    """Validate declarations and return a deterministic NEW-operation plan.

    Existing operations (legacy or shared-service) must use their original
    status/reconciliation path. This function never submits, resumes or admits.
    The trusted caller owns registry, review and source-observation provenance.
    """
    require(existing_operation is None, 'existing_operation_requires_original_reader')
    request = detached(envelope)
    fields(request, {'schema', 'intent', 'task_id', 'host_id', 'project_id', 'profile_id',
                     'repository', 'owner', 'registry_sha256', 'source', 'command',
                     'resources', 'review'}, 'envelope_fields')
    require(request['schema'] == ENVELOPE and request['intent'] == 'new-operation',
            'new_operation_only')
    identifier(request['task_id'], 'task_id')
    registry = validate_registry(registry)
    pin(request['registry_sha256'], 'registry_pin')
    require(request['registry_sha256'] == digest(registry), 'registry_drift')
    matches = [p for p in registry['profiles'] if p['project_id'] == request['project_id']
               and p['profile_id'] == request['profile_id']]
    require(len(matches) == 1, 'unknown_project_profile')
    profile = matches[0]
    require(request['host_id'] == registry['host_id']
            and all(request[key] == profile[key] for key in ('repository', 'owner')),
            'project_identity')
    source = request['source']
    fields(source, {'branch', 'head', 'tree', 'worktree', 'uid', 'gid'}, 'source_fields')
    require(type(source['branch']) is str and re.fullmatch(
        r'(?:feature|bugfix|hotfix)/[a-z0-9][a-z0-9._/-]{0,159}', source['branch'])
        and all(part not in ('', '.', '..') and not part.startswith('.')
                and not part.endswith('.lock')
                for part in source['branch'].split('/'))
        and '..' not in source['branch'] and not source['branch'].endswith('.'), 'branch')
    for key in ('head', 'tree'):
        pin(source[key], key, 40)
    worktree = path(source['worktree'])
    require(path(profile['worktree_root']) in worktree.parents, 'worktree_scope')
    require(all(type(source[key]) is int and source[key] == profile[key]
                for key in ('uid', 'gid')), 'worktree_owner')
    command = request['command']
    fields(command, {'argv', 'cwd', 'timeout_seconds'}, 'command_fields')
    argv = command['argv']
    require(type(argv) is list and 1 <= len(argv) <= 128, 'argv')
    for arg in argv:
        text(arg, 'argument', 4096)
    require(argv[0] in profile['executables'], 'unqualified_executable')
    require(within(path(command['cwd']), worktree), 'cwd_scope')
    timeout = command['timeout_seconds']
    require(type(timeout) is int and 1 <= timeout <= profile['resource_limits']['timeout_seconds'],
            'timeout')
    resources = request['resources']
    fields(resources, RESOURCE_FIELDS | {'read_roots', 'write_roots'}, 'resource_fields')
    numbers = {key: resources[key] for key in RESOURCE_FIELDS}
    resource_numbers(numbers, RESOURCE_FIELDS)
    require(all(number <= profile['resource_limits'][key] for key, number in numbers.items()),
            'resource_limit')
    data = list(map(path, profile['data_roots']))
    allowed_reads = [worktree, path(profile['checkout_root']), *data]
    allowed_writes = [worktree, *data]
    for key, allowed in (('read_roots', allowed_reads), ('write_roots', allowed_writes)):
        selected = path_list(resources[key], key)
        require(all(any(within(candidate, root) for root in allowed) for candidate in selected),
                'resource_scope')
    review = request['review']
    fields(review, {'reviewer', 'disposition', 'binding_sha256', 'evidence_sha256'}, 'review_fields')
    identifier(review['reviewer'], 'reviewer')
    require(review['reviewer'] != request['owner'] and review['disposition'] == 'approved',
            'independent_review')
    for key in ('binding_sha256', 'evidence_sha256'):
        pin(review[key], 'review_pin')
    require(review['binding_sha256'] == reviewed_binding_digest(request), 'review_drift')
    observed = detached(observation)
    fields(observed, {'schema', 'host_id', 'project_id', 'repository', 'owner', 'source',
                      'clean', 'canonical_paths', 'evidence_sha256'}, 'observation_fields')
    require(observed['schema'] == OBSERVATION and observed['clean'] is True
            and observed['canonical_paths'] is True, 'source_not_qualified')
    pin(observed['evidence_sha256'], 'observation_pin')
    require(all(canonical_bytes(observed[key]) == canonical_bytes(request[key]) for key in
                ('host_id', 'project_id', 'repository', 'owner', 'source')), 'source_drift')
    request_sha = digest(request)
    service = registry['service']
    return {'schema': PLAN, 'status': 'plan-only',
            'operation_id': 'foo-project-v1-' + request_sha,
            'request_sha256': request_sha, 'request': request,
            'registry_sha256': digest(registry), 'profile_sha256': profile_digest(profile),
            'source_observation_sha256': digest(observed), 'service': service,
            'project_state_root': profile['state_root'],
            'execution_identity': {'uid': profile['uid'], 'gid': profile['gid']},
            'admission': {'authority': service['admission_authority'],
                          'root': service['admission_root'], 'policy_sha256': service['policy_sha256'],
                          'required': True, 'granted': False},
            'execution_started': False, 'accepted_delivery': False}
