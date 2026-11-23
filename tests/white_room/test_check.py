import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'white_room'))
from check import digest, evaluate
from inventory import inventory

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()

class Recovery(unittest.TestCase):
    def setUp(self):
        self.contract = {'version':1,'projects':{'uif':{'repository':'maldous/uif','discovery_complete':{'foo':True,'cloud':True}}},'requirements':[
            {'id':'github','profiles':['foo','cloud'],'projects':['uif'],'expected':{'repository':'maldous/uif'},'max_age_seconds':300},
            {'id':'windmill','profiles':['foo'],'projects':['uif'],'expected':{'tool':'foo_root_jobs','workspace':'offmenu'},'max_age_seconds':300}]}
        self.evidence = {'version':1,'contract_sha256':digest(self.contract),'project':'uif','profile':'cloud', 'observations':[
            {'id':'github','state':'compliant','actual':{'repository':'maldous/uif'},'source':'synthetic:github','observed_at':STAMP}],
            'recovery':{'repository':'maldous/uif','accepted_commit':'a'*40,'selected_commit':'a'*40,'issue':'UIF-247','owner':'cloud-b','ownership_epoch':2,'authoritative_owner':'cloud-b','authoritative_epoch':2,'remote_read_at':STAMP,'ownership_expires_at':'2026-11-09T12:05:00+00:00','workspace_isolated':True,'recoverable_work_preserved':True,'pending_operations':[],'checkpoint':{'disposition':'none','absence_verified':True},'previous_owner_fenced':True,'superseded':False,'published_checkpoint':{'remote_verified':True,'commit':'a'*40,'source':'synthetic:remote','repository':'maldous/uif','issue':'UIF-247','owner':'cloud-b','ownership_epoch':2,'observed_at':STAMP}}}
    def run_check(self):
        return evaluate(self.contract,self.evidence,self.evidence['profile'],'uif',NOW)
    def assertBlocked(self):
        self.assertFalse(self.run_check()['preflight_passed'])
    def test_cloud_continues_without_foo(self):
        result=self.run_check(); self.assertTrue(result['preflight_passed']); self.assertFalse(result['execution_authorized']); self.assertFalse(result['runtime_readiness_verified'])
    def test_foo_requires_its_dependencies(self):
        self.evidence['profile']='foo'; self.assertBlocked()
    def test_renamed_windmill_refused(self):
        self.evidence['profile']='foo'
        self.evidence['observations'].append({'id':'windmill','state':'compliant','actual':{'tool':'renamed_root','workspace':'offmenu'},'source':'synthetic:mcp','observed_at':STAMP})
        self.assertBlocked()
    def test_unknown_requirements_refused(self):
        self.contract['projects']['uif']['discovery_complete']['cloud']=False
        self.evidence['contract_sha256']=digest(self.contract); self.assertBlocked()
    def test_stale_superseded_foo(self):
        self.evidence['recovery']['selected_commit']='b'*40; self.assertBlocked()
    def test_uncertain_effect_survives_cloud_loss(self):
        self.evidence['recovery']['pending_operations']=[{'id':'original-job','state':'unknown'}]; self.assertBlocked()
    def test_worker_or_coordinator_loss_requires_fencing(self):
        self.evidence['recovery']['previous_owner_fenced']=False; self.assertBlocked()
    def test_late_old_owner_publication_refused(self):
        self.evidence['recovery']['ownership_epoch']=1; self.assertBlocked()
    def test_quota_restore_not_new_authority(self):
        self.evidence['recovery']['superseded']=True; self.assertBlocked()
    def test_unpublished_work_is_not_recovered(self):
        self.evidence['recovery']['published_checkpoint']['remote_verified']=False; self.assertBlocked()
    def test_dirty_work_must_be_preserved(self):
        self.evidence['recovery']['recoverable_work_preserved']=False; self.assertBlocked()
    def test_stale_graph_schema(self):
        self.evidence['recovery']['checkpoint']={'disposition':'resume','commit':'a'*40,'issue':'UIF-247','owner':'cloud-b','epoch':2,'compatibility_verified':False,'receipt_source':'synthetic:checkpoint'}; self.assertBlocked()
    def test_bound_checkpoint_resume(self):
        self.evidence['recovery']['checkpoint']={'disposition':'resume','commit':'a'*40,'issue':'UIF-247','owner':'cloud-b','epoch':2,'compatibility_verified':True,'receipt_source':'synthetic:checkpoint'}
        self.assertTrue(self.run_check()['preflight_passed'])
    def test_checkpoint_epoch_type_drift_refused(self):
        self.evidence['recovery']['checkpoint']={'disposition':'resume','commit':'a'*40,'issue':'UIF-247','owner':'cloud-b','epoch':2.0,'compatibility_verified':True,'receipt_source':'synthetic:checkpoint'}
        self.assertBlocked()
    def test_missing_recovery_refused(self):
        del self.evidence['recovery']; self.assertBlocked()
    def test_unavailable_dependency_states(self):
        for state in ['unknown','blocked','failed','drifted','not-applicable']:
            with self.subTest(state=state):
                self.evidence['observations'][0]['state']=state; self.assertBlocked()
    def test_stale_or_future_evidence(self):
        for t in ['2026-11-08T12:00:00+00:00','2026-11-10T12:00:00+00:00','invalid']:
            self.evidence['observations'][0]['observed_at']=t; self.assertBlocked()
    def test_duplicate_evidence_rejected(self):
        self.evidence['observations']*=2
        with self.assertRaises(ValueError): self.run_check()
    def test_contract_drift_refused(self):
        self.contract['requirements'][0]['expected']={'repository':'other'}; self.assertBlocked()
    def test_missing_expected_refused(self):
        self.contract['requirements'][0]['expected']=None; self.evidence['contract_sha256']=digest(self.contract); self.assertBlocked()
    def test_expired_owner_refused(self):
        self.evidence['recovery']['ownership_expires_at']=STAMP; self.assertBlocked()
    def test_checkout_must_match_selected_commit_and_registration(self):
        checkout = {'root': '/fixture', 'head': 'a' * 40, 'branch': 'develop',
                    'worktrees': [{'path': '/fixture', 'head': 'a' * 40,
                                   'branch_ref': 'refs/heads/develop'}]}
        def result():
            return evaluate(self.contract, self.evidence, 'cloud', 'uif', NOW, checkout=checkout)
        self.assertTrue(result()['preflight_passed'])
        checkout['worktrees'][0]['head'] = 'b' * 40
        checks = {row['id']: row['state'] for row in result()['checks']}
        self.assertEqual(checks['checkout'], 'drifted')
        checkout['worktrees'][0]['head'] = checkout['head'] = 'b' * 40
        self.assertFalse(result()['preflight_passed'])

    def test_wrong_project_rejected(self):
        self.evidence['project']='offmenu'; self.assertBlocked()
    def test_type_drift_refused(self):
        self.contract['requirements'][0]['expected']={'enabled':True}
        self.evidence['contract_sha256']=digest(self.contract)
        self.evidence['observations'][0]['actual']={'enabled':1}
        self.assertBlocked()
    def test_version_must_be_integer_one(self):
        for document in (self.contract, self.evidence):
            for invalid_version in (True, 1.0):
                with self.subTest(document=document is self.contract, version=invalid_version):
                    document['version'] = invalid_version
                    self.evidence['contract_sha256'] = digest(self.contract)
                    with self.assertRaises(ValueError):
                        self.run_check()
                    document['version'] = 1

    def test_recovery_issue_and_owner_require_nonblank_strings(self):
        for key in ('issue', 'owner'):
            for invalid_identity in (7, True, '  '):
                with self.subTest(key=key, identity=invalid_identity):
                    self.setUp()
                    context = self.evidence['recovery']
                    context[key] = context['published_checkpoint'][key] = invalid_identity
                    if key == 'owner':
                        context['authoritative_owner'] = invalid_identity
                    result = self.run_check()
                    self.assertFalse(result['preflight_passed'])
                    self.assertIn('identity', {row['id'] for row in result['checks']})

    def test_discovery_completion_requires_boolean_map(self):
        for invalid_discovery in ([], {'cloud': 'true'}):
            with self.subTest(discovery=invalid_discovery):
                self.contract['projects']['uif']['discovery_complete'] = invalid_discovery
                self.evidence['contract_sha256'] = digest(self.contract)
                with self.assertRaises(ValueError):
                    self.run_check()

    def test_invalid_freshness_refused(self):
        self.contract['requirements'][0]['max_age_seconds']=float('nan')
        with self.assertRaises(ValueError): self.run_check()
    def test_null_repository_refused(self):
        self.contract['projects']['uif']['repository']=None
        self.evidence['recovery']['repository']=None
        with self.assertRaises(ValueError): self.run_check()
    def test_publication_owner_mismatch_refused(self):
        self.evidence['recovery']['published_checkpoint']['owner']='old'
        self.assertBlocked()
    def test_unknown_observation_rejected(self):
        self.evidence['observations'].append({'id':'unrecognized'})
        with self.assertRaises(ValueError): self.run_check()
    def test_repository_contract_deliberately_incomplete(self):
        contract=json.loads((ROOT/'config/white-room.json').read_text())
        self.evidence['observations']=[]
        result=evaluate(contract,self.evidence,'cloud','uif',NOW)
        self.assertFalse(result['preflight_passed'])

class Inventory(unittest.TestCase):
    def test_read_only_dirty_checkout_and_declaration_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*args): return subprocess.run(['git','-C',tmp,*args],check=True,capture_output=True)
            git('init','-b','develop'); git('config','user.email','test@example.invalid'); git('config','user.name','Test')
            (root/'AGENTS.md').write_text('instructions'); (root/'package.json').write_text('{}')
            git('add','.'); git('commit','-m','fixture')
            linked = root / 'linked checkout'
            git('worktree', 'add', '--detach', str(linked), 'HEAD')
            git('worktree', 'lock', '--reason', 'private lock reason', str(linked))
            (root/'private.txt').write_text('do not output file contents')
            hook=root/'watcher'
            hook.write_text('#!/bin/sh\ntouch '+str(root/'hook-ran')+'\n')
            hook.chmod(0o755)
            git('config','core.fsmonitor',str(hook))
            (root/'.gitattributes').write_text('package.json filter=fixture\n')
            filter_script=root/'clean-filter'
            filter_script.write_text('#!/bin/sh\ntouch '+str(root/'filter-ran')+'\ncat\n')
            filter_script.chmod(0o755)
            git('config','filter.fixture.clean',str(filter_script))
            (root/'package.json').write_text('[]')
            before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
            result=inventory(root)
            self.assertIsNone(result['dirty']); self.assertFalse(result['remote_verified'])
            self.assertEqual(Path(result['git_common_dir']), root.resolve() / '.git')
            worktrees = {Path(row['path']): row for row in result['worktrees']}
            self.assertEqual(set(worktrees), {root.resolve(), linked.resolve()})
            self.assertEqual(worktrees[root.resolve()]['branch_ref'], 'refs/heads/develop')
            self.assertEqual(worktrees[linked.resolve()]['head'], result['head'])
            self.assertIsNone(worktrees[linked.resolve()]['branch_ref'])
            self.assertTrue(worktrees[linked.resolve()]['detached'])
            self.assertTrue(worktrees[linked.resolve()]['locked'])
            self.assertNotIn('private lock reason', json.dumps(result))
            self.assertIn('package.json',result['declaration_paths'])
            self.assertNotIn('do not output',json.dumps(result))
            self.assertFalse((root/'hook-ran').exists())
            self.assertFalse((root/'filter-ran').exists())
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir() if p.is_file()})
            git('config','remote.origin.promisor','true')
            with self.assertRaises(ValueError): inventory(root)

if __name__=='__main__': unittest.main()
