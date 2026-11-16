import copy,unittest
from uif import publication_handoff as h
class Tests(unittest.TestCase):
 def setUp(self):
  self.x={'schema':h.SCHEMA,'project':'Cloud','route':'cloud_worker_assets','operation_id':'graph-20261116-1230','source':{'kind':'generated_snapshot','identity':'a'*64,'tree':'b'*64},'artifact':{'sha256':'c'*64,'size_bytes':123},'destination_ref':'cloud.codex-production','publisher_ref':'cloud.current-original-publisher','evidence_refs':['review:exact','checks:original','rights:scope']}
 def test_plan_is_not_effect(self):self.assertFalse(h.normalize(self.x)['publication_executed'])
 def test_no_foo_publish(self):self.assertFalse(h.catalog()['foo_publish_supported'])
 def test_no_publish_action(self):
  with self.assertRaises(ValueError):h.main('publish',self.x)
 def test_unexpected_credential_field(self):
  self.x['token']='not-a-real-token'
  with self.assertRaises(ValueError):h.normalize(self.x)
 def test_project_route_mismatch(self):
  self.x['project']='ANQR'
  with self.assertRaises(ValueError):h.normalize(self.x)
 def test_short_source_pin(self):
  self.x['source']={'kind':'git','identity':'a'*7,'tree':'b'*40}
  with self.assertRaises(ValueError):h.normalize(self.x)
 def test_boolean_size(self):
  self.x['artifact']['size_bytes']=True
  with self.assertRaises(ValueError):h.normalize(self.x)
 def test_no_generic_release_success(self):
  self.x['project']='CIX';self.x['route']='project_release';r=self.receipt()
  with self.assertRaises(ValueError):h.bind_receipt(self.x,r)
 def receipt(self):return {'handoff_sha256':h.normalize(self.x)['handoff_sha256'],'operation_id':self.x['operation_id'],'destination_ref':self.x['destination_ref'],'artifact_sha256':'c'*64,'native_receipt_ref':'native:original-operation','readback_ref':'live:matching-version','outcome':'succeeded'}
 def test_receipt_binding_not_attestation(self):self.assertFalse(h.bind_receipt(self.x,self.receipt())['independently_verified'])
 def test_changed_destination_refusal(self):
  r=self.receipt();r['destination_ref']='cloud.other'
  with self.assertRaises(ValueError):h.bind_receipt(self.x,r)
 def test_immutable_copy(self):
  r=h.normalize(self.x);r['handoff']['artifact']['size_bytes']=2;self.assertEqual(self.x['artifact']['size_bytes'],123)
if __name__=='__main__':unittest.main()
