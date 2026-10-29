"""Explicit human-confirmed continuation of an uncertain clone; never clones again.

Run only after the user identifies and authorizes the existing candidate. The
original uncertain action and its error remain in the journal for inspection.
"""
import argparse
import fcntl
from c1ai.core import Core
from c1ai.macos_backend import MacOSBackend
from c1ai.macos_ipc import state_directory
from c1ai.journal import PENDING


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request-id', required=True)
    p.add_argument('--candidate-id', required=True)
    p.add_argument('--human-confirmed', action='store_true', required=True)
    args = p.parse_args()
    root = state_directory()
    lock = (root / 'owner.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        p.error('Stop the idle owner before explicit administrative confirmation; no concurrent writer is allowed')
    core = Core(MacOSBackend(root), root)
    record = core.journal.get(request_id=args.request_id)
    if record['status'] not in PENDING or record['request'].get('command') != 'images' or record['request'].get('action') != 'create_candidate':
        p.error('Only an uncertain create_candidate operation can be confirmed')
    before = core.backend.observe([])
    if not core.backend._writable_session(before['target']):
        p.error('The current Session is not configured')
    result = core._action(record['operation_id'], 'images.human_confirmed_selection',
        {'human_confirmed': True, 'candidate_id': args.candidate_id},
        lambda: core.backend._call('confirm_candidate', expected=before['target'],
            source=record['target'], candidate_id=args.candidate_id))
    after = core.backend._observation(result)
    core.backend.register_candidate(after['target'], record['target'])
    core.journal.update(record['operation_id'], status='applied_not_restored', after=after,
        reconciliation={'outcome':'applied_not_restored', 'target':record['target'],
            'evidence':{'source':'explicit_human_confirmation_and_native_readback',
                'candidate_target':after['target'], 'original_action_certainty':'unknown',
                'no_clone_replayed':True}})
    print({'status':'confirmed_existing_candidate', 'candidate_id':args.candidate_id})


if __name__ == '__main__':
    main()
