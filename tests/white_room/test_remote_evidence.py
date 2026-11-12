import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'/'white_room'))
from remote_evidence import Target, ReadFailure, reconstruct
from check import evaluate

A, B, C = 'a'*40, 'b'*40, 'c'*40
TEAM = '12345678-1234-1234-1234-123456789abc'
NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

def work(key='UIF-247'):
    return dict(id=key,teamId=TEAM,projectId=None,assigneeId=None,statusType='backlog',
                title='Scope',description='Private prose, not output',updatedAt=NOW.isoformat(),
                relations={'blockedBy':[]},relations_complete=True)

class Fake:
    def __init__(self):
        self.calls=[]
        self.responses={
          'github.branch':dict(name='develop',commit={'sha':B}),
          'github.commit':dict(sha=A,tree={'sha':C}),
          'github.pr':dict(number=8,merged=True,state='closed',merge_commit_sha=A,
             base={'repo':{'full_name':'maldous/uif'},'ref':'develop','sha':C},head={'sha':B}),
          'github.compare':dict(status='ahead',base_commit={'sha':A},merge_base_commit={'sha':A},commits=[{'sha':B}]),
          'linear.issue':work()}
        self.change=None
    def read(self, kind, target):
        self.calls.append((kind,copy.deepcopy(target)))
        d=copy.deepcopy(self.responses[kind])
        if isinstance(d,Exception): raise d
        if self.change: d=self.change(kind,target,d,self.calls)
        return d

class RemoteEvidence(unittest.TestCase):
    def setUp(self):
        self.target=Target('d'*64,A,'UIF-247',TEAM,None,8)
        self.reader=Fake()
    def run_adapter(self): return reconstruct(self.target,self.reader,lambda:NOW)
    def inconclusive(self):
        result=self.run_adapter();self.assertEqual(result['status'],'inconclusive');self.assertEqual(result['observations'],[]);return result
    def test_exact_reads_and_no_authority(self):
        result=self.run_adapter();self.assertEqual(result['status'],'observed')
        self.assertEqual([x[0] for x in self.reader.calls],['github.branch','github.commit','github.pr','github.compare','linear.issue','github.branch','github.pr','linear.issue'])
        self.assertEqual(self.reader.calls[3][1],{'repository':'maldous/uif','base':A,'head':B})
        self.assertEqual(len(result['receipts']),8)
        for key in ('execution_authorized','runtime_readiness_verified','saved_json_is_authenticated','discovery_complete'):self.assertIs(result[key],False)
        for key in ('ownership','checkpoint','pending_operations'):self.assertEqual(result[key],{'state':'unknown'})
        self.assertNotIn('recovery',result)
    def test_wrong_caller_target_refused_before_read(self):
        for field,value in [('repository','maldous/offmenu'),('profile','foo'),('project','cix'),('ref','main'),('issue','CIX-3'),('acceptance_pr',True)]:
            target=copy.copy(self.target);object.__setattr__(target,field,value)
            with self.assertRaises(ValueError):reconstruct(target,self.reader,lambda:NOW)
        self.assertEqual(self.reader.calls,[])
    def test_wrong_repo_project_issue_response(self):
        for kind, mutate in [('github.pr',lambda d:d['base']['repo'].update(full_name='wrong/repo')),
                             ('linear.issue',lambda d:d.update(projectId=TEAM)),
                             ('linear.issue',lambda d:d.update(id='UIF-1'))]:
            with self.subTest(kind=kind):
                self.reader=Fake();mutate(self.reader.responses[kind]);self.inconclusive()
    def test_branch_movement(self):
        self.reader.change=lambda k,t,d,c: dict(name='develop',commit={'sha':C}) if k=='github.branch' and len(c)>1 else d
        self.inconclusive()
    def test_pr_movement(self):
        def change(k,t,d,c):
            if k=='github.pr' and len(c)>4:d['head']['sha']=C
            return d
        self.reader.change=change;self.inconclusive()
    def test_issue_owner_scope_and_dependencies_movement(self):
        for field,value in [('assigneeId',TEAM),('description','changed'),('relations',{'blockedBy':[{'id':'UIF-2'}]})]:
            with self.subTest(field=field):
                self.reader=Fake()
                def change(k,t,d,c):
                    if k=='linear.issue' and len(c)>5:d[field]=value
                    return d
                self.reader.change=change;self.inconclusive()
    def test_dependencies_read_twice_and_change_detected(self):
        self.reader.responses['linear.issue']['relations']['blockedBy']=[{'id':'CIX-2'}]
        def change(k,t,d,c):
            if k=='linear.issue' and t['id']=='CIX-2':
                d=work('CIX-2')
                if len(c)>8:d['statusType']='completed'
            return d
        self.reader.change=change;self.inconclusive()
        self.assertEqual(sum(t.get('id')=='CIX-2' for k,t in self.reader.calls),2)
    def test_denial_timeout_no_retry_or_fallback(self):
        for category in ['denied','timeout','unavailable']:
            self.reader=Fake();self.reader.responses['github.branch']=ReadFailure(category)
            self.assertEqual(self.inconclusive()['reason'],category)
            self.assertEqual(len(self.reader.calls),1)
    def test_error_body_not_leaked(self):
        self.reader.responses['github.branch']=RuntimeError('SECRET-token')
        self.assertNotIn('SECRET',json.dumps(self.inconclusive()))
    def test_missing_partial_response(self):
        for kind in self.reader.responses:
            self.reader=Fake();self.reader.responses[kind]={};self.inconclusive()
    def test_missing_dependencies_is_unknown(self):
        del self.reader.responses['linear.issue']['relations'];self.inconclusive()
    def test_oversized_response_is_unknown(self):
        self.reader.responses['github.branch']['extra']='x'*262144;self.inconclusive()
    def test_secret_extra_fields_and_source_url_not_propagated(self):
        for data in self.reader.responses.values():data.update(secret='SECRET',source='https://evil.invalid/SECRET',authenticated=True)
        encoded=json.dumps(self.run_adapter());self.assertNotIn('SECRET',encoded);self.assertNotIn('evil.invalid',encoded)
    def test_unmerged_pr_not_source_acceptance(self):
        self.reader.responses['github.pr']['merged']=False;self.inconclusive()
    def test_diverged_and_partial_compare_refused(self):
        for changes in [{'status':'diverged'},{'commits':[]},{'commits':[{'sha':C}]}]:
            self.reader=Fake();self.reader.responses['github.compare'].update(changes);self.inconclusive()
    def test_identical_compare(self):
        self.reader.responses['github.branch']['commit']['sha']=A
        self.reader.responses['github.compare'].update(status='identical',commits=[])
        self.assertEqual(self.run_adapter()['status'],'observed')
    def test_expired_or_backwards_clock(self):
        for offset in [-1,301]:
            times=iter([NOW,NOW+timedelta(seconds=offset)])
            r=reconstruct(self.target,self.reader,lambda:next(times));self.assertEqual(r['status'],'inconclusive')
    def test_partial_dependencies_refused(self):
        for changes in [{'relations_complete':False},{'relations':{'blockedBy':[],'pageInfo':{'hasNextPage':True},'totalCount':10}},{'relations':{'blockedBy':[],'totalCount':1}}]:
            self.reader=Fake();self.reader.responses['linear.issue'].update(changes);self.inconclusive()
    def test_missing_completeness_refused(self):
        del self.reader.responses['linear.issue']['relations_complete'];self.inconclusive()
    def test_reader_cannot_mutate_receipt_targets(self):
        def change(k,t,d,c):
            t['connector_auth']='SECRET'
            return d
        self.reader.change=change
        r=self.run_adapter();self.assertEqual(r['status'],'observed');self.assertNotIn('SECRET',json.dumps(r))
    def test_clock_must_be_monotonic_within_window(self):
        times=iter([NOW,NOW+timedelta(seconds=240),NOW+timedelta(seconds=60)])
        r=reconstruct(self.target,self.reader,lambda:next(times));self.assertEqual(r['status'],'inconclusive')
    def test_stock_contract_stays_blocked(self):
        contract=json.loads((ROOT/'config/white-room.json').read_text())
        self.assertFalse(evaluate(contract,self.run_adapter(),'cloud','uif',NOW)['preflight_passed'])

if __name__=='__main__':unittest.main()
