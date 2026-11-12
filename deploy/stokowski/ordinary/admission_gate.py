"""Enforce the installed phase before shared admission can open the Pool."""
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
import sys

from uif import shared_job_admission as shared
from uif.host_jobs import NAMESPACE

from ordinary_start_window import check_window, new_work
from project_binding import project_scope

PROOF_IDS = ('uif-neutral-epoch1-proof-io', 'uif-neutral-epoch1-proof-cancel')
RETIRED_PROOF_IDS = (*PROOF_IDS, 'uif-neutral-epoch1-proof-io-recovery1',
                     'uif-neutral-epoch1-proof-io-recovery2')
PROOF_MANIFEST = {'path': '/etc/uif-stokowski/neutral-proof-requests.json',
                  'sha256': '60bbb7c1084899d78971c71f7a922531c3564d1792dd1dff4be719147a596255'}
DESCRIPTOR = {'path': '/etc/uif-stokowski/foo-user-exec.json',
              'sha256': '18892ec946a8c001ba3dd9f53388f90ba9a16c8fc8141a5698a1e2cdb0c6eb8a'}


def allowed(request, phase, manifest=None, descriptor=None):
    binding = request.get('binding')
    action = request.get('action')
    if (request.get('schema') != shared.SCHEMA or not isinstance(binding, dict)
            or action not in {'admit', 'start', 'status', 'cancel', 'finish'}):
        return False
    fields = {'schema', 'action', 'binding'}
    if action == 'start' or (action == 'cancel' and 'request' in request):
        fields.add('request')
    if action == 'finish':
        fields |= {'lease_id', 'receipt_sha256', 'outcome', 'child_tree_gone'}
    if set(request) != fields:
        return False
    job = binding.get('job_id')
    if phase in {'ordinary', 'drain'}:
        return (isinstance(job, str) and job not in RETIRED_PROOF_IDS
                and (phase == 'ordinary' or action in {'status', 'cancel', 'finish'}))
    if phase != 'proof' or job not in PROOF_IDS:
        return False
    requests = manifest['requests']
    run, = (item for item in requests if item['job_id'] == job and item['action'] == 'run')
    expected = dict(namespace=NAMESPACE, workspace='foo', job_id=job,
                    descriptor_sha256=DESCRIPTOR['sha256'], request_sha256=run['request_sha256'],
                    command_sha256=sha256(run['command'].encode()).hexdigest(),
                    mode=run['mode'], timeout_seconds=run['timeout_seconds'], resources=run['resources'],
                    capacity_policy_sha256=descriptor['policy_sha256'], runner_sha256=descriptor['runner_sha256'])
    if shared._encode(binding) != shared._encode(expected):
        return False
    if action == 'cancel' and not any(item['job_id'] == job and item['action'] == 'cancel' for item in requests):
        return False
    if action == 'start' or 'request' in request:
        return shared._encode(request.get('request')) == shared._encode(run)
    return True


def process(raw, phase):
    request = shared._decode(raw, shared.MAX_REQUEST)
    if raw != shared._encode(request):
        raise ValueError('Noncanonical shared request')
    manifest = descriptor = None
    if phase == 'proof':
        manifest = shared._decode(shared._registered_file(PROOF_MANIFEST).read_bytes())
        descriptor = shared._decode(shared._registered_file(DESCRIPTOR).read_bytes())
    if not allowed(request, phase, manifest, descriptor):
        raise ValueError('Shared admission scope refused')
    creates_work = phase == 'ordinary' and request['action'] in {'admit', 'start'}
    if creates_work and not project_scope(request['binding']):
        raise ValueError('Project-work scope refused')
    with new_work() if creates_work else nullcontext():
        authority = shared.SharedJobAdmission.load(shared.REGISTRATION)
        if creates_work:
            check_window()
        if phase == 'ordinary' and request['action'] == 'admit':
            return authority.handle(request, before_admit_commit=check_window)
        return authority.handle(request)


def main(phase):
    if len(sys.argv) != 1 or sys.stdin.isatty():
        return 2
    try:
        raw = sys.stdin.buffer.read(shared.MAX_REQUEST + 1)
        response = process(raw, phase)
        sys.stdout.buffer.write(shared._encode(response) + b'\n')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return 2
    return 0
