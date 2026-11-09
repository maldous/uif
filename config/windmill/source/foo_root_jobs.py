#requirements:
#paramiko==5.1.0
#wmill==1.850.0
"""Owner-only Foo root transport. Deploy disabled until host protocol qualification."""
import io
import json
import re
import time
import paramiko
import wmill

HOST = '192.168.1.3'
USER = 'offmenu-wm'
KEY_VARIABLE = 'u/admin/foo_ssh_key_20261027'
HOST_KEY = 'AAAAC3NzaC1lZDI1NTE5AAAAIAgv17VNww/UZBfIohmz0jg0Xmim5s8FJuPyi4L9qZ0H'
ENABLED = True


def main(action: str = 'status', job_id: str = '', command: str = '',
         cwd: str = '/', timeout_seconds: int = 60):
    if not ENABLED:
        raise RuntimeError('ROOT_ROUTE_NOT_ACTIVATED')
    if action not in ('run', 'status', 'cancel'):
        raise ValueError('INVALID_ACTION')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', job_id):
        raise ValueError('INVALID_JOB_ID')
    request = {'version': 1, 'action': action, 'job_id': job_id}
    if action == 'run':
        if not isinstance(command, str) or not command or '\0' in command or len(command.encode()) > 32000:
            raise ValueError('INVALID_COMMAND')
        if not isinstance(cwd, str) or not cwd.startswith('/') or '\0' in cwd or len(cwd) > 4096:
            raise ValueError('INVALID_CWD')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
            raise ValueError('INVALID_TIMEOUT')
        request.update(command=command, cwd=cwd, timeout_seconds=timeout_seconds)
    elif command:
        raise ValueError('COMMAND_NOT_ALLOWED')
    import base64
    client = paramiko.SSHClient()
    client.get_host_keys().add(HOST, 'ssh-ed25519', paramiko.Ed25519Key(data=base64.b64decode(HOST_KEY)))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    secret = wmill.get_variable(KEY_VARIABLE)
    with io.StringIO(secret) as stream:
        key = paramiko.Ed25519Key.from_private_key(stream)
    secret = None
    try:
        client.connect(HOST, port=22, username=USER, pkey=key,
                       allow_agent=False, look_for_keys=False,
                       timeout=5, banner_timeout=5, auth_timeout=5)
        key = None
        channel = client.get_transport().open_session(timeout=5)
        channel.exec_command('foo-root-exec-v1')
        channel.sendall(json.dumps(request, separators=(',', ':')).encode())
        channel.shutdown_write()
        deadline = time.monotonic() + (timeout_seconds + 30 if action == 'run' else 30)
        output = bytearray()
        error_seen = False
        while True:
            if channel.recv_ready():
                output.extend(channel.recv(65536))
                if len(output) > 262144:
                    raise RuntimeError('ROOT_TRANSPORT_OUTPUT_LIMIT_RECONCILE_JOB_ID')
            if channel.recv_stderr_ready():
                error_seen = bool(channel.recv_stderr(65536)) or error_seen
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('ROOT_TRANSPORT_TIMEOUT_RECONCILE_JOB_ID')
            time.sleep(0.02)
        code = channel.recv_exit_status()
        if code != 0 or error_seen:
            raise RuntimeError('ROOT_TRANSPORT_FAILED_RECONCILE_JOB_ID')
        result = json.loads(output)
        if not isinstance(result, dict) or result.get('version') != 1 or result.get('job_id') != job_id:
            raise RuntimeError('ROOT_RECEIPT_INVALID_RECONCILE_JOB_ID')
        if result.get('status') not in ('not_found', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'unknown', 'cancel_requested'):
            raise RuntimeError('ROOT_RECEIPT_STATE_INVALID_RECONCILE_JOB_ID')
        return result
    finally:
        client.close()
