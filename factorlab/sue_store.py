"""Private append-only SUE files. Permissions/hashes are not immutable storage."""
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from .sue_plan import Invalid, MAX_BYTES, canonical, digest, load, require, text, seal, open_document, exact_keys


def safe_path(path):
    p = Path(path).absolute()
    require('..' not in p.parts, 'unsafe_path')
    for q in (p, *p.parents):
        require(not q.is_symlink(), 'unsafe_path')
    return p


def private_root(path):
    p = safe_path(path)
    require(p.is_dir() and stat.S_IMODE(p.stat().st_mode) == 0o700, 'private_root_required')
    return p


def read(path, limit=MAX_BYTES):
    p = safe_path(path); private_root(p.parent)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_size <= limit and stat.S_IMODE(s.st_mode) == 0o600, 'private_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            raw = f.read(limit + 1)
        require(len(raw) <= limit, 'document_bound')
        return raw
    finally:
        os.close(fd)


def publish(path, raw):
    p = safe_path(path); private_root(p.parent)
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'document_bound')
    tmp = p.parent / ('.pending-' + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        # Hard link publishes a fully written inode atomically, never overwrites.
        os.link(tmp, p, follow_symlinks=False)
        directory = os.open(p.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        tmp.unlink()


def write_json(path, obj):
    publish(path, canonical(obj))


class Store:
    def __init__(self, root, operation, create=False):
        text(operation); self.root = private_root(root); self.path = safe_path(self.root / operation)
        if create:
            try: self.path.mkdir(mode=0o700)
            except FileExistsError: pass
        private_root(self.path)

    @contextmanager
    def lock(self):
        p = self.path / 'lock'
        try: fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError: raise Invalid('operation_locked_manual_reconciliation_required') from None
        os.close(fd)
        try: yield
        finally: p.unlink()

    def put(self, name, obj):
        text(name); write_json(self.path / name, obj)

    def get(self, name):
        text(name); return load(read(self.path / name))

    def raw(self, name):
        text(name); return read(self.path / name)

    def names(self):
        names = set()
        for p in self.path.iterdir():
            require(not p.is_symlink() and p.is_file(), 'unsafe_store_entry')
            names.add(p.name)
        require(not any(n.startswith('.pending-') for n in names), 'partial_publication_manual_review')
        return names

    def attempts(self):
        names = self.names(); numbers = sorted(int(n[1:-7]) for n in names if n.startswith('a') and n.endswith('.intent'))
        require(numbers == list(range(1, len(numbers) + 1)), 'attempt_sequence')
        expected = {'binding.json', 'lock'}
        for i in numbers:
            expected.update(('a%06d' % i) + ext for ext in ('.intent', '.body', '.receipt', '.status', '.retry-authorization'))
        require(names <= expected, 'unknown_store_entry')
        result = []
        for i in numbers:
            stem = 'a%06d' % i; intent = self.get(stem + '.intent')
            receipt = open_document(self.get(stem + '.receipt'), 'sue-attempt-receipt') if stem + '.receipt' in names else None
            exact_keys(intent, ('symbol', 'endpoint', 'limit', 'plan_sha256', 'started', 'attempt', 'operation', 'campaign_sha256', 'budget_before', 'reserved'))
            if receipt:
                exact_keys(receipt, ('state', 'error', 'stop', 'status', 'finished', 'elapsed', 'charged', 'worker', 'validation', 'payload_sha256', 'intent_sha256', 'replayable'))
            if stem+'.status' in names:
                status = open_document(self.get(stem+'.status'), 'sue-http-status')
                exact_keys(status, ('status','at','intent_sha256'))
                require(status['intent_sha256'] == digest(canonical(intent)), 'status_tampering')
                if receipt: require(status['status'] == receipt['status'], 'status_mismatch')
            if receipt:
                require(receipt['intent_sha256'] == digest(canonical(intent)), 'intent_tampering')
                if receipt['payload_sha256']:
                    require(digest(self.raw(stem + '.body')) == receipt['payload_sha256'], 'payload_tampering')
            result.append((stem, intent, receipt))
        return result
