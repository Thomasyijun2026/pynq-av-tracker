"""Independent manager wired to the exact V5 / IO10 / Coordinator12 contract.

V2 durable commands, unknown-result handling and HTTP parser are reused. This
entry never replaces a live manager/worker, and connection never initializes.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from av_managed_observer_V2 import (ManagedSession as Previous, manager_admission,
    handler, command_response, UnknownCommand, ThreadingHTTPServer)
from av_runtime_backend_V2 import Backend
from av_runtime_binding_V2 import Launcher, readiness


class ManagedSession(Previous):
    def __init__(self, backend, stop, observer_factory):
        super().__init__(backend, stop, observer_factory)
        # Constructing the previous pure Launcher has no side effects. Only this
        # exact V5 launcher is ever ticked; no shared module globals are changed.
        self.launcher = Launcher(backend, self.clock)

    def _view(self):
        b, now = self.backend, self.clock()
        if self.closed or now >= self.stop:
            return None, None, dict(status='MANAGER_ENDED', ready=False, automatic_initialize=False)
        if b.host.boot() != b.cfg['boot_id']:
            return None, None, dict(status='BOOT_CHANGED', ready=False, automatic_initialize=False)
        record = b.intent()
        if record is None: return None, None, copy.deepcopy(self.last)
        if (record.get('status') != 'SPAWNED' or record.get('plan_sha256') != b.plan['plan_sha256']
                or record.get('config_sha256') != b.plan['config_sha256']):
            return record, None, b.observe(record, now)
        seen = b.observation(record)
        life = readiness(record, seen, self.clock())
        if self.clock() >= b.cfg['no_new_operation_monotonic']:
            life = dict(life, status='OPERATION_CUTOFF', ready=False)
        return record, seen, life


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if set(config)!={'plan','predecessors','backend_folder','manager_stop_monotonic','manager_sources','page_sha256'}:
        raise ValueError('Exact reviewed manager configuration required')
    backend=Backend(config['plan'],config['backend_folder'],config['predecessors'])
    source=Path(__file__).resolve().parent
    if 'av_managed_observer_V3.py' not in config['manager_sources']:
        raise ValueError('Manager V3 source identity missing')
    for name,sha in config['manager_sources'].items():
        if Path(name).name!=name or hashlib.sha256((source/name).read_bytes()).hexdigest()!=sha:
            raise ValueError('Manager source SHA mismatch: '+name)
    page=(source/'av_observer_ui_V7.html').read_bytes()
    if hashlib.sha256(page).hexdigest()!=config['page_sha256']:raise ValueError('Page SHA mismatch')
    from av_observer_result_V1 import Observer
    with backend.host.lock(backend.host.path(backend.cfg['session_root']+'/runtime_manager.lock')):
        admission=manager_admission(backend,config['manager_stop_monotonic'])
        session=ManagedSession(backend,config['manager_stop_monotonic'],Observer)
        server=ThreadingHTTPServer(('0.0.0.0',8764),handler(session,page))
        server.daemon_threads=False
        owner=dict(boot_id=backend.host.boot(),pid=os.getpid(),
            start_ticks=backend.host.process(os.getpid())['actual_start_ticks'],admission=admission,
            port=8764,hardware_opened=False,started_monotonic=backend.host.now(),stop_monotonic=session.stop,
            manager_version=3,runtime_version=5,automatic_initialize=False)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        try:
            backend.write(backend.folder/'manager_owner.json',owner,exclusive=True)
            thread.start()
            while backend.host.now()<session.stop:
                session.tick()
                time.sleep(min(.8,max(0.,session.stop-backend.host.now())))
        finally:
            session.close()
            if thread.is_alive():server.shutdown()
            server.server_close()
            if thread.ident is not None:thread.join(3)
            backend.write(backend.folder/'closed.json',dict(owner,http_closed=not thread.is_alive(),
                runtime_interrupted=False,hardware_opened=False,ended_monotonic=backend.host.now()))


if __name__=='__main__':main()
