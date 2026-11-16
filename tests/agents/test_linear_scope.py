import copy
import hashlib
import json
import unittest

from uif.linear_scope import (
    ScopeNormalizationError,
    native_scope_sha256,
    normalize_issue_scope,
)


FIRST = ('<issue id="11111111-1111-4111-8111-111111111111" label="SYN-1" '
         'href="https://linear.app/example/issue/SYN-1/synthetic-one?view=full&x=%2F#part" />')
SECOND = ('<issue id="22222222-2222-4222-8222-222222222222" label="SYN-2" '
          'href="https://linear.app/example/issue/SYN-2/synthetic-two" />')
FIRST_MD = '[SYN-1](https://linear.app/example/issue/SYN-1/synthetic-one?view=full&x=%2F#part)'
SECOND_MD = '[SYN-2](https://linear.app/example/issue/SYN-2/synthetic-two)'


def issue(description):
    return {"title": "Synthetic café 🧪", "description": description,
            "identifier": "SYN-3", "id": "33333333-3333-4333-8333-333333333333",
            "extra": {"status": "unchanged"}}


def expected(value):
    fields = [value[key] for key in ("id", "identifier", "title", "description")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


class LinearScopeTests(unittest.TestCase):
    def test_native_serialization_uses_exact_order_unicode_and_default_separators(self):
        value = issue("Retain\r\n\tUnicode é and trailing spaces  \n")
        self.assertEqual(native_scope_sha256(value), expected(value))
        compact = json.dumps([value[key] for key in
                              ("id", "identifier", "title", "description")],
                             ensure_ascii=False, separators=(",", ":"))
        self.assertNotEqual(native_scope_sha256(value), hashlib.sha256(compact.encode()).hexdigest())

    def test_two_links_full_urls_order_other_bytes_and_fields_are_preserved(self):
        value = issue(f"Before\r\n{SECOND}\tthen {FIRST}\n<div>literal</div>  \n")
        before = copy.deepcopy(value)
        target = issue(f"Before\r\n{SECOND_MD}\tthen {FIRST_MD}\n<div>literal</div>  \n")
        result = normalize_issue_scope(value, expected(target))
        self.assertEqual(result, target)
        self.assertEqual(value, before)
        self.assertIsNot(result, value)
        self.assertIs(result["extra"], value["extra"])

    def test_already_matching_literal_renderer_example_is_not_rewritten(self):
        value = issue(f"Literal example: `{FIRST}` and <b>HTML</b>")
        self.assertIs(normalize_issue_scope(value, expected(value)), value)

    def test_non_issue_html_is_never_canonicalized(self):
        value = issue("<b>literal</b>&amp;\n")
        self.assertIs(normalize_issue_scope(value, expected(value)), value)
        with self.assertRaises(ScopeNormalizationError):
            normalize_issue_scope(value, expected(issue("literal&\n")))

    def test_changed_requirements_or_other_scope_fields_cannot_match_old_scope(self):
        target = issue(FIRST_MD)
        for value in [issue(FIRST + "\nNew requirement"),
                      dict(issue(FIRST), title="Changed title"),
                      dict(issue(FIRST), identifier="SYN-4"),
                      dict(issue(FIRST), id="44444444-4444-4444-8444-444444444444")]:
            with self.subTest(value=value), self.assertRaises(ScopeNormalizationError):
                normalize_issue_scope(value, expected(target))

    def test_unknown_renderer_syntax_is_not_rewritten(self):
        variants = [FIRST.replace('id="11111111-1111-4111-8111-111111111111"', 'id="invalid"'),
                    FIRST.replace(' label=', ' extra="unknown" label='),
                    FIRST.replace('label="SYN-1"', 'label="SYN-0"'),
                    FIRST.replace('https://linear.app/', 'https://example.invalid/'),
                    FIRST.replace('id="', "id='", 1),
                    FIRST.replace(' />', '/>'),
                    FIRST.replace('id="111', 'id="AAA', 1)]
        for markup in variants:
            with self.subTest(markup=markup), self.assertRaises(ScopeNormalizationError):
                normalize_issue_scope(issue(markup), expected(issue(FIRST_MD)))

    def test_valid_projection_does_not_unescape_or_modify_other_markup(self):
        value = issue(FIRST + ' <b data-x="&amp;">literal</b>')
        target = issue(FIRST_MD + ' <b data-x="&amp;">literal</b>')
        self.assertEqual(normalize_issue_scope(value, expected(target)), target)

    def test_null_description_can_match_but_is_not_coerced_to_empty(self):
        value = issue(None)
        self.assertIs(normalize_issue_scope(value, expected(value)), value)
        with self.assertRaises(ScopeNormalizationError):
            normalize_issue_scope(value, expected(issue("")))

    def test_bad_expected_hash_is_a_typed_error(self):
        for digest in (None, "", "A" * 64, "0" * 63, 1):
            with self.subTest(digest=digest), self.assertRaises(ScopeNormalizationError):
                normalize_issue_scope(issue(FIRST), digest)

    def test_invalid_native_shape_is_a_typed_error(self):
        for value in (None, [], {}, dict(issue(""), title=1),
                      dict(issue(""), description={}), dict(issue(""), id=None)):
            with self.subTest(value=value), self.assertRaises(ScopeNormalizationError):
                normalize_issue_scope(value, "0" * 64)


if __name__ == "__main__":
    unittest.main()
