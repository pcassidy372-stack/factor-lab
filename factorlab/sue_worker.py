"""POSIX owned request worker: bounded pipes, monotonic cancellation, no shell.

Only HTTPS uses this boundary. Keys live in inherited memory, never argv/files.
The child clears its environment before constructing a Requests session.
"""
import multiprocessing
import os
import time
from .sue_plan import Invalid, require


class WorkerFailure(Exception):
    def __init__(self, code, status, receipt):
        self.code, self.status, self.receipt = code, status, receipt
        super().__init__(code)


def _child(pipe, task, args):
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1')
    # No accidental exception text/credential output from adapters.
    with open(os.devnull, 'wb', buffering=0) as null:
        os.dup2(null.fileno(), 1); os.dup2(null.fileno(), 2)
    def emit(status):
        require(type(status) is int and 100 <= status <= 599, 'invalid_status')
        pipe.send(('status', status))
    try:
        result = task(*args, emit)
        pipe.send(('result', result))
    except Invalid as exc:
        pipe.send(('error', str(exc)))
    except BaseException:
        pipe.send(('error', 'transport_failure'))
    finally:
        pipe.close()


def run_owned(task, args, timeout, on_status=lambda _: None):
    require(os.name == 'posix' and 0 < timeout <= 20, 'worker_platform_or_budget')
    ctx = multiprocessing.get_context('fork')
    parent, child = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_child, args=(child, task, args), daemon=True)
    started = time.monotonic(); status = None; result = None; code = None
    worker.start(); child.close()
    receipt = {'pid': worker.pid, 'owned': True, 'terminated': False, 'killed': False}
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                code = 'worker_deadline_unknown'; break
            if parent.poll(min(remaining, .02)):
                try: kind, value = parent.recv()
                except EOFError:
                    code = 'worker_exit_unknown'; break
                if kind == 'status':
                    require(status is None and type(value) is int and 100 <= value <= 599, 'worker_protocol')
                    status = value; on_status(status)
                elif kind == 'result': result = value; break
                elif kind == 'error': code = value; break
                else: raise Invalid('worker_protocol')
            elif not worker.is_alive():
                code = 'worker_exit_unknown'; break
    finally:
        # Only this Process object/PID is signalled, never unrelated processes.
        if worker.is_alive():
            worker.join(.05)
        if worker.is_alive():
            receipt['terminated'] = True; worker.terminate(); worker.join(.5)
        if worker.is_alive():
            receipt['killed'] = True; worker.kill(); worker.join(.5)
        parent.close()
        receipt.update(exit_code=worker.exitcode, alive=worker.is_alive(), elapsed=time.monotonic()-started)
        require(not worker.is_alive(), 'owned_worker_cleanup_failed')
        worker.close()
    if code is not None: raise WorkerFailure(code, status, receipt)
    require(result is not None, 'worker_protocol')
    return result, receipt
