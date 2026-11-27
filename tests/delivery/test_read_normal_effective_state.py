from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import read_normal_effective_state as read


class NormalEffectiveState(unittest.TestCase):
    def setUp(self):
        self.stage = '/opt/uif-stokowski/uif1313-normal-test'
        exe = self.stage + '/venv/bin/python3.12'
        self.values = {'Id':read.UNIT, 'NeedDaemonReload':False, 'ActiveState':'inactive', 'SubState':'dead',
                       'Job':[0,'/'], 'Conditions':[], 'Asserts':[], 'MainPID':0,'ControlPID':0,
                       'NRestarts':0,'ExecMainStartTimestampMonotonic':21,
                       'ExecStart':[[exe,[exe,'-I','-S','-B',self.stage+'/launch.py'],
                                     False,0,0,0,0,0,0,0]]}
        self.original = read.NormalEffectiveOriginal(self.stage, 'a'*64, 'a'*64, 21, 0, (), ())
        self.calls = []
        self.enterContext(patch.object(read.subprocess,'check_output',side_effect=self.bus_output))

    def bus_output(self, argv, **options):
        self.calls.append(argv)
        self.assertEqual(argv[5:9], ['--auto-start=no','get-property',read.BUS,read.OBJECT])
        name = argv[-1]
        signature = next(s for _, n, s in read.FIELDS if name == n)
        return json.dumps({'type':signature,'data':self.values[name]}).encode()

    def test_typed_manager_no_action_exact_target_and_fresh_no_start(self):
        result = read.observe_normal_effective(self.original)
        self.assertEqual(result['status'], 'effective_normal_stopped_same_original_observation')
        self.assertFalse(result['installation_accepted'])
        self.assertFalse(result['native_startup_verified'])
        self.assertEqual(len(self.calls), 2 * len(read.FIELDS))

    def test_disk_only_stale_manager_and_old_condition_are_not_effective(self):
        self.assertEqual(read.observe_normal_effective(
            replace(self.original, observed_disk_dropin_sha256='b'*64))['status'], 'disk_release_differs')
        self.assertFalse(self.calls)
        self.values['NeedDaemonReload'] = True
        self.assertEqual(read.observe_normal_effective(self.original)['status'],
                         'disk_matches_manager_reload_needed')
        self.values['NeedDaemonReload'] = False
        self.values['Conditions'] = [['ConditionPathExists',False,False,'/old/ACTIVATION_AUTHORIZED',0]]
        self.assertEqual(read.observe_normal_effective(self.original)['status'],
                         'disk_matches_effective_manager_differs')

    def test_no_start_and_no_job_are_observed_not_inferred(self):
        self.values['Job'] = [5,'/org/freedesktop/systemd1/job/5']
        self.assertEqual(read.observe_normal_effective(self.original)['status'], 'effective_normal_not_stopped')
        self.values['Job'] = [0,'/']
        self.values['ExecMainStartTimestampMonotonic'] = 22
        self.assertEqual(read.observe_normal_effective(self.original)['status'],
                         'effective_normal_start_history_changed')
