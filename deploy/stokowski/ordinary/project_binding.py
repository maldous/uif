"""Constrain persistent neutral runs to the selected UIF project snapshot."""
import re

INPUT_ROOT = '/var/lib/uif-foo/project-work-89e0f76/input'
OUTPUT_ROOT = '/var/lib/uif-foo/project-work-89e0f76/output'
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")

def project_scope(binding):
    job = binding.get("job_id")
    resources = binding.get("resources")
    mode = binding.get("mode")
    if (not isinstance(job, str) or JOB_ID.fullmatch(job) is None
            or not isinstance(resources, dict) or mode not in {"read", "write"}):
        return False
    writable = [f"{OUTPUT_ROOT}/{job}"] if mode == "write" else []
    return (resources.get("read_roots") == [INPUT_ROOT]
            and resources.get("write_roots") == writable)
