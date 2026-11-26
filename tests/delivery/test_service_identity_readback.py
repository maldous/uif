import unittest

from uif.effect_authority import AuthorityIdentity
from uif.service_identity_readback import (
    IdentityReadbackExpectation, IdentityReadbackUnavailable, verifies_same_role,
)


class IdentityReadbackTests(unittest.TestCase):
    def case(self, role):
        ident = AuthorityIdentity(authority_id="authority", session_id=role,
                                  deployment_sha256="a"*64, policy_sha256="b"*64)
        config = IdentityReadbackExpectation(role, ident, 3, "c"*64, True)
        status = dict(role=role, authority_id="authority", session_id=role,
                      deployment_sha256="a"*64, policy_sha256="b"*64,
                      installation_sha256="c"*64, commissioned_sha256="c"*64,
                      coordinator_epoch=3, stopped=True, extra="not exported")
        return config, status

    def test_redacted_verified_status_for_both_roles(self):
        for role in ("morgan", "native"):
            cfg, status = self.case(role)
            self.assertEqual(verifies_same_role(cfg, before=status, after=dict(status)),
                             dict(role=role, coordinator_epoch=3, stopped=True))

    def test_wrong_role_identity_deployment_epoch_or_stop_refuses_either_phase(self):
        cfg, status = self.case("morgan")
        changes = dict(role="control", authority_id="other", session_id="native",
                       deployment_sha256="d"*64, policy_sha256="d"*64,
                       installation_sha256="d"*64, commissioned_sha256=None,
                       coordinator_epoch=True, stopped=False)
        for key, value in changes.items():
            drift = dict(status, **{key: value})
            for before, after in ((drift, status), (status, drift)):
                with self.subTest(key=key), self.assertRaisesRegex(
                        IdentityReadbackUnavailable, "same_runtime_role_identity_unverified"):
                    verifies_same_role(cfg, before=before, after=after)

    def test_credential_expiry_or_provenance_not_inferred_from_status(self):
        cfg, status = self.case("native")
        self.assertNotIn("renewed", verifies_same_role(cfg, before=status, after=status))
        with self.assertRaisesRegex(IdentityReadbackUnavailable, "expectation_unavailable"):
            verifies_same_role(status, before=status, after=status)
