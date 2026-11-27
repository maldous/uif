from dataclasses import asdict
from unittest import TestCase
from unittest.mock import Mock, patch

from uif.effect_authority import ClaimFence, Scope, SourcePin
from uif.handoff_store import Hold, decode, wire
from uif.quota_handoff import LinearKey
from uif.stokowski_cloudflare.publication_control_read import PublicationControlRead
from uif.stokowski_cloudflare.remote_authority import AccessTransport, AuthorityUnavailable


class PublicationControlReadTest(TestCase):
    def setUp(self):
        self.current = asdict(ClaimFence(
            scope=Scope('repository', LinearKey('12345678-1234-1234-1234-123456789abc',
                '22345678-1234-1234-1234-123456789abc', '32345678-1234-1234-1234-123456789abc')),
            source=SourcePin('maldous/offmenu','develop','a'*40,'b'*40,'c'*40),
            owner_session='native', coordinator_epoch=1, generation=2, lease_id='lease',
            authority_id='realm', deployment_sha256='d'*64, policy_sha256='e'*64))
        self.read = PublicationControlRead(AccessTransport('https://authority.example.org','/never/read'))
        self.reply = Mock(status=200)
        self.reply.getheader.return_value = 'application/json'
        self.https = self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection'))
        self.https.return_value.getresponse.return_value = self.reply
        self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.protected_record',
                                return_value={'client_id':'synthetic-id','client_secret':'synthetic-fixture'}))

    def call(self, **kw):
        return self.read(fence=kw.get('fence',self.current), attempt_id=kw.get('attempt','try-1'))

    def test_exact_authenticated_read_and_false_are_not_writes_or_retries(self):
        for value in (True, False):
            self.reply.read.return_value = wire({'current':value})
            self.assertEqual(self.call(), {'current':value})
        self.assertEqual(self.https.call_count, 2)
        sent = self.https.return_value.request.call_args_list
        for call in sent:
            self.assertEqual(call.args, ('POST','/v1/publication/fence/read'))
            self.assertEqual(decode(call.kwargs['body']), {'fence':self.current,'attempt_id':'try-1'})

    def test_no_fallback_for_bad_input_untrusted_reply_or_transport_failure(self):
        for kw in ({'attempt':'white space'},{'fence':{**self.current,'extra':0}}):
            with self.assertRaises(Hold):
                self.call(**kw)
        self.https.assert_not_called()
        self.reply.read.return_value = wire({'current':1})
        with self.assertRaisesRegex(Hold,'reply_unknown'):
            self.call()
        self.https.return_value.getresponse.side_effect = OSError()
        with self.assertRaises(AuthorityUnavailable):
            self.call()
        self.assertEqual(self.https.call_count, 2)
