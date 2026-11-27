"""Pure detached normal archive packer; does not create an approval or read host files."""

from io import BytesIO
import tarfile

import stage_commissioned_normal_runtime as stage


def render_detached_normal_stage(members):
    """Package caller-owned verified offline bytes into #171's exact format.

    No supplied member may include credential material. The normal config
    carries only its protected reference. Caller verifies full independent
    release acceptance, native shelves and custody outside this renderer.
    Result contains archive bytes for private custody and digest metadata only;
    it cannot become a NormalRuntimeStageApproval or authorize a stage.
    """
    require = stage.guard.require
    require(type(members) is dict and 0 < len(members) <= stage.MAX_FILES,
            'expected supplied detached normal members')
    total = 0
    for name, value in members.items():
        require(stage._member_path_allowed(name) and type(value) is tuple and len(value) == 2,
                'unexpected normal stage archive member')
        raw, mode = value
        require(type(raw) is bytes and type(mode) is int
                and mode in (0o444, 0o555), 'expected immutable normal stage member')
        total += len(raw)
        require(total <= stage.MAX_ARCHIVE, 'oversized normal stage membership')
    projection = stage._project_member_map(members)
    packed = BytesIO()
    with tarfile.open(fileobj=packed, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(members):
            raw, mode = members[name]
            leaf = tarfile.TarInfo(name)
            leaf.mtime = leaf.uid = leaf.gid = 0
            leaf.size, leaf.mode = len(raw), mode
            archive.addfile(leaf, BytesIO(raw))
            require(packed.tell() <= stage.MAX_ARCHIVE, 'oversized detached normal archive')
    blob = packed.getvalue()
    require(len(blob) <= stage.MAX_ARCHIVE, 'oversized detached normal archive')
    return blob, {'status': 'detached_candidate_only',
                  'archive_sha256': stage.guard.digest(blob),
                  'members_sha256': stage.guard.plan_digest(projection),
                  'member_count': len(projection),
                  'production_acceptance': 'not_evaluated'}
