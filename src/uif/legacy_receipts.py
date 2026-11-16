"""Normalize retained legacy source-free evidence without granting acceptance.

This pure receipt builder performs no reads, writes, claims, dispatch or Git
publication. The caller must authenticate the original published tuple and use
the existing native receiver/fence for any effect. It never manufactures the
modern reviewed_disposition field or treats a mutable latest settlement as input.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .handoff_store import decode, require, wire

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_KEY_BYTES = 256
MAX_POINTER_BYTES = 1024
MAX_REFERENCE_HITS = 512
MAX_UNIQUE_HASHES = 256
MAX_REFERENCE_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 128 * 1024
MAX_SELECTED_INPUTS = 64
MAX_ROUTING_METADATA_BYTES = 64 * 1024
MAX_ROUTING_METADATA_NODES = 512
HEX = re.compile(r'[0-9a-f]{64}')
HASH_TOKEN = re.compile(r'(?<![0-9a-fA-F])[0-9a-f]{64}(?![0-9a-fA-F])')
DOCUMENTARY_FIELDS = ('data_sources', 'claims', 'verification')
LEGACY_POLICY_SHA256 = 'a032e8f09c4c67da9a328e19fe25af3c3e1d0a52b143aa70524e3281fb1754d1'
KNOWN_SHARED_POLICY_HASHES = frozenset({
    LEGACY_POLICY_SHA256,
    '7b54579a72c16c504bf4a82d875a0ca770d851787ba62035e446ec673e6db54f',
    '4b88a8be6d6f4d8bbd721a1f22bd3d47250d5391be220b5ed77011249e226fa9',
    'f8e19235eb095cfc754519f580c6bb35675baedbf4bf15d42b4a969becec90c4',
})
BINDING_KEYS = {'candidate', 'human_context_sha256', 'invocation', 'issue',
                'issue_id', 'kind', 'run', 'scope_sha256', 'state'}
NATIVE_RETAINED_RECIPE = 'stokowski-retained-json-sort-keys-ensure-ascii-false/1'


@dataclass(frozen=True)
class ArchivePin:
    record_id: str
    file_sha256: str


def native_retained_record_id(archive):
    """Exact valid-JSON recipe from installed publication.py fc60cbd9:505-520.

    Its writer at628-633 includes state/run/binding/report/result_text/artifacts
    and optional context in this complete object. Default separators matter;
    this is NOT the raw file-byte hash or the compact receipt-wire digest.
    """
    require(type(archive) is dict, 'legacy_native_record_object')
    try:
        encoded = json.dumps(archive, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        require(False, 'legacy_native_record_encoding')
    return sha256(encoded).hexdigest()


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _text(value):
    return type(value) is str and bool(value) and len(value) <= 512


def _positive(value):
    return type(value) is int and value > 0


def _utf8(value):
    try:
        return value.encode('utf-8')
    except UnicodeError:
        require(False, 'legacy_unicode_encoding')


def _routing_snapshot(value):
    """Freeze bounded original published routing metadata, never current grants."""
    used, nodes = 0, 0
    def visit(item, depth=0):
        nonlocal used, nodes
        nodes += 1
        require(nodes <= MAX_ROUTING_METADATA_NODES and depth <= 32,
                'legacy_routing_metadata_nodes')
        kind = type(item)
        if kind is str:
            require(len(item) <= MAX_ROUTING_METADATA_BYTES, 'legacy_routing_metadata_size')
            used += len(_utf8(item))
        elif kind is dict:
            require(len(item) <= MAX_ROUTING_METADATA_NODES, 'legacy_routing_metadata_nodes')
            for key, child in item.items():
                require(type(key) is str, 'legacy_routing_metadata_type')
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif kind is list:
            require(len(item) <= MAX_ROUTING_METADATA_NODES, 'legacy_routing_metadata_nodes')
            for child in item:
                visit(child, depth + 1)
        else:
            require(kind in (int, float, bool, type(None)), 'legacy_routing_metadata_type')
            used += 32
        require(used <= MAX_ROUTING_METADATA_BYTES, 'legacy_routing_metadata_size')
    require(type(value) is dict, 'legacy_routing_metadata_type')
    visit(value)
    return decode(wire(value), MAX_ARCHIVE_BYTES)


def _archive(raw, pin):
    require(type(pin) is ArchivePin and _hex(pin.record_id) and _hex(pin.file_sha256),
            'legacy_archive_pin')
    require(type(raw) is bytes and len(raw) <= MAX_ARCHIVE_BYTES, 'legacy_archive_size')
    require(sha256(raw).hexdigest() == pin.file_sha256, 'legacy_archive_file_hash')
    value = decode(raw, MAX_ARCHIVE_BYTES)
    require(type(value.get('context')) is dict and type(value.get('report')) is dict,
            'legacy_archive_shape')
    return value


def _binding(value):
    require(type(value) is dict and set(value) == BINDING_KEYS, 'legacy_review_binding_shape')
    require(value['kind'] == 'source-free' and value['state'] == 'independent-review',
            'legacy_review_route')
    require(value['candidate'] == value['issue_id'], 'legacy_source_free_candidate_identity')
    require(all(_text(value[key]) for key in ('issue', 'issue_id', 'candidate', 'invocation'))
            and _positive(value['run'])
            and all(_hex(value[key]) for key in ('scope_sha256', 'human_context_sha256')),
            'legacy_review_binding_values')


def _artifacts(value):
    # Empty is an explicit authentic legacy selection, not missing evidence.
    require(type(value) is list and len(value) <= 128, 'legacy_artifact_list')
    seen = set()
    for artifact in value:
        require(type(artifact) is dict and set(artifact) == {'path', 'sha256', 'size_bytes'},
                'legacy_artifact_shape')
        name = artifact['path']
        require(_text(name) and not name.startswith('/') and '\\' not in name
                and all(ord(char) >= 32 for char in name)
                and all(part not in ('', '.', '..') for part in name.split('/'))
                and name not in seen and _hex(artifact['sha256'])
                and type(artifact['size_bytes']) is int and artifact['size_bytes'] >= 0,
                'legacy_artifact_values')
        seen.add(name)


def _references(report):
    """Return documentary hash tokens and exact RFC6901 pointers, no bodies."""
    found, total_bytes, hits = {}, 0, 0

    def visit(value, path):
        nonlocal total_bytes, hits
        require(len(path) <= 32, 'legacy_documentary_depth')
        if type(value) is str:
            pointer = '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in path)
            pointer_bytes = len(_utf8(pointer))
            require(pointer_bytes <= MAX_POINTER_BYTES, 'legacy_documentary_pointer_size')
            for match in HASH_TOKEN.finditer(value):
                token = match.group()
                hits += 1
                require(hits <= MAX_REFERENCE_HITS, 'legacy_documentary_hit_limit')
                if token not in found:
                    require(len(found) < MAX_UNIQUE_HASHES, 'legacy_documentary_hash_limit')
                    found[token] = set()
                if pointer not in found[token]:
                    total_bytes += pointer_bytes + len(token)
                    require(total_bytes <= MAX_REFERENCE_BYTES, 'legacy_documentary_aggregate_limit')
                    found[token].add(pointer)
        elif type(value) is list:
            for index, child in enumerate(value):
                visit(child, path + (index,))
        elif type(value) is dict:
            for key, child in value.items():
                require(len(_utf8(key)) <= MAX_KEY_BYTES, 'legacy_documentary_key_size')
                visit(child, path + (key,))

    for field in DOCUMENTARY_FIELDS:
        if field in report:
            visit(report[field], ('report', field))
    return {token: sorted(paths) for token, paths in found.items()}


def build_legacy_receipt(*, original_bytes, review_bytes, original_pin, review_pin,
                         published_review_binding, selected_input_sha256=(),
                         verified_record_identity=None, excluded_sha256=(),
                         selected_input_classification=None, published_review_routing=None):
    """Build an exact, bounded receipt from independently authenticated inputs.

    A directory name alone proves no digest. verified_record_identity is an
    optional explicit selection of native_retained_record_id, checked for BOTH
    originals. Arbitrary callbacks/JSON flags cannot assert digest validation.
    Without that recipe selection only exact file-hash or explicit documentary
    input joins can qualify; an unverified directory name remains a hint.
    """
    original = _archive(original_bytes, original_pin)
    review = _archive(review_bytes, review_pin)
    # Freeze the supplied external tuple instead of retaining mutable caller data.
    _binding(published_review_binding)
    binding = decode(wire(published_review_binding))
    routing = _routing_snapshot(binding if published_review_routing is None else published_review_routing)
    require(BINDING_KEYS.issubset(routing)
            and wire({key: routing[key] for key in BINDING_KEYS}) == wire(binding),
            'legacy_published_routing_core_mismatch')
    require(wire(review.get('binding')) == wire(binding)
            and wire(review['report'].get('routing')) == wire(routing),
            'legacy_published_review_binding_mismatch')
    require('reviewed_disposition' not in review['report'], 'use_modern_disposition_route')
    require(review.get('state') == 'independent-review'
            and review['report'].get('verdict') == 'approve', 'legacy_review_verdict')
    require((original.get('state'), original['report'].get('verdict'))
            in {('assess', 'approve'), ('investigate', 'complete')}, 'legacy_original_stage')
    require(_positive(original.get('run')) and type(review.get('run')) is int
            and original['run'] == review['run'] == binding['run'], 'legacy_run_join')
    for archive in (original, review):
        context = archive['context']
        require(context.get('issue') == binding['issue']
                and context.get('issue_id') == binding['issue_id']
                and context.get('issue_scope_sha256') == binding['scope_sha256'],
                'legacy_issue_scope_join')
        require(_text(context.get('session_id')), 'legacy_session_identity')
    require(original['context']['session_id'] != review['context']['session_id'],
            'legacy_distinct_review_session')
    old_binding = original.get('binding')
    require(old_binding is None or (type(old_binding) is dict
            and old_binding.get('kind') == 'source-free'
            and old_binding.get('issue_id') == binding['issue_id']
            and type(old_binding.get('run')) is int and old_binding['run'] == binding['run']
            and old_binding.get('scope_sha256') == binding['scope_sha256']),
            'legacy_original_route')
    _artifacts(original.get('artifacts'))
    _artifacts(review.get('artifacts'))
    require(type(selected_input_sha256) is tuple and type(excluded_sha256) is tuple
            and len(selected_input_sha256) <= MAX_SELECTED_INPUTS
            and len(excluded_sha256) <= MAX_SELECTED_INPUTS
            and all(_hex(item) for item in selected_input_sha256 + excluded_sha256)
            and len(set(selected_input_sha256)) == len(selected_input_sha256),
            'legacy_selected_input_pins')
    excluded = set(excluded_sha256) | KNOWN_SHARED_POLICY_HASHES
    require(not (set(selected_input_sha256) & excluded), 'policy_hash_is_not_selected_input')
    if selected_input_sha256:
        require(type(selected_input_classification) is dict
                and set(selected_input_classification) == set(selected_input_sha256)
                and all(type(value) is str and value == 'selected-documentary-input'
                        for value in selected_input_classification.values()),
                'legacy_selected_input_classification_required')
    else:
        require(selected_input_classification is None or selected_input_classification == {},
                'legacy_unselected_input_classification')
    classification_snapshot = {token: selected_input_classification[token]
                               for token in selected_input_sha256}
    before_refs, after_refs = _references(original['report']), _references(review['report'])
    record_identity_verified = False
    if verified_record_identity is not None:
        require(verified_record_identity is native_retained_record_id, 'unsupported_legacy_record_recipe')
        for archive, pin in ((original, original_pin), (review, review_pin)):
            before = wire(archive)
            require(verified_record_identity(archive) == pin.record_id,
                    'legacy_record_identity_mismatch')
            require(wire(archive) == before, 'legacy_record_validator_mutated_input')
        record_identity_verified = True
    proof = []
    if original_pin.file_sha256 in after_refs:
        proof.append(dict(kind='original-evidence-file-sha256', sha256=original_pin.file_sha256,
                          review_pointers=after_refs[original_pin.file_sha256]))
    if record_identity_verified and original_pin.record_id in after_refs:
        proof.append(dict(kind='verified-native-record-id', sha256=original_pin.record_id,
                          review_pointers=after_refs[original_pin.record_id]))
    for token in selected_input_sha256:
        require(token in before_refs and token in after_refs, 'legacy_selected_input_join_missing')
        proof.append(dict(kind='selected-documentary-input-sha256', sha256=token,
                          original_pointers=before_refs[token], review_pointers=after_refs[token]))
    receipt = dict(
        schema='uif-legacy-source-free-receipt/1',
        status='bounded-input-join' if proof else 'input-join-unresolved',
        issue=binding['issue'], issue_id=binding['issue_id'], run=binding['run'],
        scope_sha256=binding['scope_sha256'], binding=binding,
        original_published_routing=routing,
        original_published_routing_sha256=sha256(wire(routing)).hexdigest(),
        routing_metadata_grants_current_authority=False,
        original_archive=original_pin.__dict__.copy(), review_archive=review_pin.__dict__.copy(),
        original_stage=original['state'], original_verdict=original['report']['verdict'],
        review_stage=review['state'], review_verdict=review['report']['verdict'],
        original_session=original['context']['session_id'], review_session=review['context']['session_id'],
        selected_artifacts=original['artifacts'], selected_artifacts_sha256=sha256(wire(original['artifacts'])).hexdigest(),
        proof=proof, record_identity_verified=record_identity_verified,
        record_identity_recipe=NATIVE_RETAINED_RECIPE if record_identity_verified else None,
        directory_reference_without_digest_proof=(not record_identity_verified
            and original_pin.record_id in after_refs),
        published_tuple_authentication_required=True, current_scope_and_custody_check_required=True,
        artifact_payloads_verified=False, selected_input_payloads_verified=False,
        selected_input_classification=classification_snapshot,
        additional_policy_hash_classification_required=True,
        receiving_authorized=False, gate_clearance=False, full_issue_acceptance=False,
        git_publication_authorized=False, runtime_installed=False)
    encoded = wire(receipt)
    require(len(encoded) <= MAX_RECEIPT_BYTES, 'legacy_receipt_size')
    receipt['receipt_sha256'] = sha256(encoded).hexdigest()
    require(len(wire(receipt)) <= MAX_RECEIPT_BYTES, 'legacy_receipt_size')
    return receipt
