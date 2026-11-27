from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools/delivery'))
import render_detached_normal_stage as render

stage = render.stage


class DetachedNormalStageRender(unittest.TestCase):
    def inputs(self):
        return {name:(name.encode(), 0o555 if name == 'venv/bin/python3.12' else 0o444)
                for name in sorted(stage.REQUIRED)}

    def test_order_independent_archive_round_trips_actual_stage_parser(self):
        files = self.inputs()
        archive, receipt = render.render_detached_normal_stage(files)
        self.assertEqual((archive, receipt), render.render_detached_normal_stage(
            dict(reversed(list(files.items())))))
        pins = stage.NormalRuntimeStageApproval('a'*64, 'uif1313-normal-fixture',
                receipt['archive_sha256'], receipt['members_sha256'], 'b'*64, 'c'*64)
        self.assertEqual(stage._members(archive, pins), files)
        self.assertEqual(receipt['production_acceptance'], 'not_evaluated')
        self.assertNotIn('operation_id', receipt)

    def test_missing_or_invalid_data_refused(self):
        for invalid in ({'../escape': (b'invalid', 0o444)}, {'venv': (b'collision', 0o444)},
                        {stage.RECEIPT:(b'forged receipt',0o444)}, {'launch.py':(b'code',0o644)}):
            with self.subTest(invalid=invalid), self.assertRaises(stage.guard.Held):
                render.render_detached_normal_stage(self.inputs() | invalid)
        files = self.inputs()
        del files['venv/bin/python3.12']
        with self.assertRaisesRegex(stage.guard.Held, 'incomplete'):
            render.render_detached_normal_stage(files)
