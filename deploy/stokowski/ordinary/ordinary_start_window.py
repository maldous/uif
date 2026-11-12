"""Gate new ordinary work and fence in-flight launch calls during drain."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time

from uif.host_jobs import _read, _trusted_ancestry, decode

ROOT = Path('/var/lib/uif-stokowski-cutover')
LOCK = ROOT / 'ordinary-start.lock'
WINDOW = ROOT / 'ordinary-window.json'


@contextmanager
def new_work():
    _trusted_ancestry(LOCK, 0)
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != 0 or opened.st_gid != 0
                or stat.S_IMODE(opened.st_mode) != 0o644 or opened.st_nlink != 1):
            raise ValueError('Unexpected ordinary start lock custody')
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        current = LOCK.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('Ordinary start lock changed')
        check_window()
        yield
    finally:
        os.close(fd)


def check_window():
    _trusted_ancestry(WINDOW, 0)
    window = decode(_read(WINDOW, 0))
    if window == {'schema': 'uif-neutral-ordinary-persistent/1', 'mode': 'persistent'}:
        return
    if set(window) != {'schema', 'not_before_utc_epoch', 'new_work_expires_utc_epoch'}:
        raise ValueError('Unexpected ordinary window fields')
    start, end = window['not_before_utc_epoch'], window['new_work_expires_utc_epoch']
    if (window['schema'] != 'uif-neutral-ordinary-window/1'
            or type(start) is not int or type(end) is not int
            or not 0 < start <= time.time() < end):
        raise ValueError('Ordinary new-work window closed')
