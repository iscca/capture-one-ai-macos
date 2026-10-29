"""Transport and protection regressions; these do not qualify Capture One editing."""
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time

import pytest

from c1ai import macos_ipc as ipc
from c1ai.macos_backend import MacOSBackend

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS transport and path rules')


@pytest.fixture
def short_root():
    with tempfile.TemporaryDirectory(prefix='c1-', dir='/tmp') as path:
        yield Path(path)


def test_frame_accepts_fragmented_utf8():
    left, right = socket.socketpair()
    value = {'path': '测试照片'}
    payload = json.dumps(value, ensure_ascii=False).encode()
    wire = struct.pack('<I', len(payload)) + payload
    def writer():
        with left:
            for byte in wire:
                left.sendall(bytes([byte]))
    worker = threading.Thread(target=writer)
    worker.start()
    with right:
        assert ipc.receive_frame(right, time.monotonic()+2) == value
    worker.join()


@pytest.mark.parametrize('size', [0, ipc.MAX_FRAME+1])
def test_frame_rejects_unbounded_size(size):
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack('<I', size))
        with pytest.raises(ipc.PipeError):
            ipc.receive_frame(right, time.monotonic()+1)


def test_disconnect_after_dispatch_never_resends(short_root):
    address = str(short_root/'test.sock')
    requests = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(address)
        Path(address).chmod(0o600)
        server.listen(2)
        def peer():
            conn, _ = server.accept()
            with conn:
                deadline = time.monotonic()+2
                hello = ipc.receive_frame(conn, deadline)
                ipc.send_frame(conn, {**hello,'owner_pid':os.getpid()}, deadline)
                requests.append(ipc.receive_frame(conn, deadline)['request'])
                # Simulate an accepted write whose caller loses the result.
        worker = threading.Thread(target=peer)
        worker.start()
        with pytest.raises(ipc.RequestUncertain) as caught:
            ipc.send_request({'command':'adjust','request_id':'one'}, address=address, autostart=False)
        worker.join()
    assert [r['request_id'] for r in requests] == ['one']
    assert caught.value.as_result()['query']['request_id'] == 'one'
    assert caught.value.as_result()['replay_allowed'] is False


def test_shared_socket_directory_is_rejected(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ipc.PipeError, match='private'):
        ipc.send_request({'command':'context'}, address=str(tmp_path/'bad.sock'), autostart=False)


def test_macos_paths_keep_case_and_backslashes():
    from c1ai.journal import same_scope
    def target(document, image):
        return {'document':{'path':document},'image':{'path':image}}
    assert not same_scope(target('/Photos/A','/Photos/a.tif'),target('/Photos/a','/Photos/a.tif'))
    assert not same_scope(target('/Photos/A','/Photos/a\\b.tif'),target('/Photos/A','/Photos/a/b.tif'))
    assert same_scope(target('/Photos/./A','/Photos/a.tif'),target('/Photos/A','/Photos/a.tif'))


def test_unregistered_original_cannot_be_written(tmp_path):
    backend = MacOSBackend(tmp_path)
    target = {'instance':{'version':'16.8.6.25'},'document':{'id':'session','kind':'session','path':'/session'},
              'image':{'path':'/image.tif'},'variant':{'id':'source'}}
    (tmp_path/'macos-config.json').write_text(json.dumps({'experimental_scalar_writes':True,
        'version':'16.8.6.25','document':target['document']}))
    assert backend.capabilities_for({'target':target})['fields']['exposure']['writable'] is False
    with pytest.raises(Exception, match='candidate'):
        backend.set_values({'exposure':1},{'target':target,'values':{'exposure':0}})


def test_unsupported_module_refuses_before_creating_operation(tmp_path):
    from c1ai.core import Core
    core = Core(MacOSBackend(tmp_path),tmp_path)
    result = core.dispatch({'command':'adjust','module':'curves','patch':{'action':'reset'},'request_id':'unsupported'})
    assert result['status']=='refused'
    assert core.journal.list()==[]


def test_reconcile_does_not_accept_unrelated_values(tmp_path, monkeypatch):
    backend = MacOSBackend(tmp_path)
    target = {'document':{'path':'/session'},'image':{'path':'/image'},'variant':{'id':'candidate'}}
    record = {'request':{'command':'adjust','module':'exposure','patch':{'exposure':0.2}},
              'target':target,'before':{'values':{'exposure':0,'contrast':0}}}
    monkeypatch.setattr(backend,'observe',lambda fields:{'target':target,'values':{'exposure':0.5,'contrast':0}})
    result = backend.reconcile(record)
    assert result['outcome']=='unknown'
    assert result['evidence']['bounded_to_baseline_or_patch'] is False


def test_reconcile_records_partial_application_without_replaying(tmp_path, monkeypatch):
    backend = MacOSBackend(tmp_path)
    target = {'document':{'path':'/session'},'image':{'path':'/image'},'variant':{'id':'candidate'}}
    record = {'request':{'command':'adjust','module':'white_balance','patch':{'temperature':4800,'tint':2.8}},
              'target':target,'before':{'values':{'temperature':5000,'tint':0}}}
    monkeypatch.setattr(backend,'observe',lambda fields:{'target':target,'values':{'temperature':4800.0039,'tint':0.00009}})
    result = backend.reconcile(record)
    assert result['outcome']=='applied_not_restored'
    assert result['evidence']['partial_application'] is True
    assert result['evidence']['requested_result_verified'] is False
    assert result['evidence']['read_only'] is True
    assert result['evidence']['replayed'] is False
