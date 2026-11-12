"""Bind the executable ledger driver to the registered UIF production adapters."""

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from uif.admission import _record
from uif.ledger import RowKey
from uif.ledger_coordinator import CoordinatorBindings, OperationPlan
from uif.owner_runtime import OwnerAuthority, _owned_file
from uif.publication import GitReadback, PublicationRequest, Publisher, PublisherCommand, PublisherReceiptPaths
from uif.review import NativeReviewBackend, ReviewAdapter
from uif.review_launcher import ReviewSettings
from uif.review_selector import ReviewWorkerRegistration, decode_review_spec, invoke_review
from uif.source import SourceHold, exact_path, file_digest
from uif.supervisor import _trusted
from uif.validation import StageBinding, ValidationSpec, decode, read_evidence, wire


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@dataclass(frozen=True)
class _Response:
    status_code: int
    content: bytes

    @property
    def text(self):
        return self.content.decode("utf-8")

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise SourceHold("uif_ledger_windmill_http_status")


class _WindmillClient:
    def __init__(self, base_url, token_path):
        url = urlsplit(base_url)
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path.rstrip("/") != "/api"
                or any(character.isspace() for character in base_url)):
            raise SourceHold("uif_ledger_windmill_endpoint")
        self.base_url = base_url.rstrip("/")
        self.token_path = exact_path(token_path)
        _owned_file(self.token_path)

    def _request(self, method, path, params=None, payload=None):
        _owned_file(self.token_path)
        token = read_evidence(self.token_path, uid=os.geteuid(), limit=4096).decode("ascii").strip()
        if not token or any(not 33 <= ord(character) <= 126 for character in token):
            raise SourceHold("uif_ledger_windmill_token")
        url = self.base_url + path
        if params is not None:
            url += "?" + urlencode(params)
        request = Request(url, data=payload, method=method,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            response = build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return _Response(response.code, response.read(1048577))

    async def get(self, path, *, follow_redirects):
        if follow_redirects is not False:
            raise SourceHold("uif_ledger_windmill_redirect")
        return await asyncio.to_thread(self._request, "GET", path)

    async def post(self, path, *, params, json, follow_redirects):
        if follow_redirects is not False:
            raise SourceHold("uif_ledger_windmill_redirect")
        return await asyncio.to_thread(self._request, "POST", path, params, wire(json))


def _path_pin(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise SourceHold("uif_ledger_adapter_file_reference")
    path = exact_path(reference["path"])
    if file_digest(path) != reference["sha256"]:
        raise SourceHold("uif_ledger_adapter_file_pin")
    return path


def _fields(record, names):
    if not isinstance(record, dict) or set(record) != set(names.split()):
        raise SourceHold("uif_ledger_adapter_registration")
    return record


def _operation_inputs(authority, key, runtime, field):
    entry, registered = authority.admission._entry(RowKey(key.store_id, key.row_id))
    if registered != runtime:
        raise SourceHold("uif_ledger_adapter_runtime")
    reference = entry.get(field)
    if not isinstance(reference, dict) or not exact_path(reference.get("path", "")).is_relative_to(runtime):
        raise SourceHold("uif_ledger_adapter_operation_input")
    return _record(reference)


def _settings(authority, reference):
    settings = _fields(_record(reference), "schema owner_registration supervisor windmill review publisher")
    if settings["schema"] != "uif-ledger-adapter-registration/1" or settings["owner_registration"] != authority.registration:
        raise SourceHold("uif_ledger_adapter_owner_registration")
    return settings


def _review_settings(authority, reference):
    review = _fields(_record(reference),
                     "schema owner_registration executable_path evidence_uid provider model effort windmill")
    if review["schema"] != "uif-ledger-review-registration/1" or review["owner_registration"] != authority.registration:
        raise SourceHold("uif_ledger_adapter_review_registration")
    _fields(review["windmill"], "script_path runner_receipts runner_uid inspect_version")
    return review


def _review_registration(authority, review, executable, executable_sha256):
    windmill = review["windmill"]
    if exact_path(review["executable_path"]) != executable:
        raise SourceHold("uif_ledger_adapter_review_executable")
    return ReviewWorkerRegistration(
        ReviewSettings(review["provider"], review["model"], review["effort"]),
        executable, executable_sha256, review["evidence_uid"],
        authority.pool_database_path, "foo", windmill["script_path"],
        exact_path(windmill["runner_receipts"]), windmill["runner_uid"], windmill["inspect_version"],
    )


def run_review_worker(argv, *, invoked_path, owner, review):
    """Use only the fixed executable and the same pinned review registration as the driver."""
    authority = OwnerAuthority.load(owner["path"], owner["sha256"])
    invoked_path = _trusted(invoked_path, executable=True)
    registration = _review_registration(authority, _review_settings(authority, review),
                                        invoked_path, file_digest(invoked_path))
    outcome = invoke_review(argv, authority=authority, registration=registration, invoked_path=invoked_path)
    return 0 if outcome["status"] == "succeeded" else 1


def bind_ledger_coordinator(authority):
    """Build real adapters from a separately pinned, owner-selected registration."""
    try:
        reference = {"path": os.environ["UIF_LEDGER_ADAPTER_REGISTRATION"],
                     "sha256": os.environ["UIF_LEDGER_ADAPTER_REGISTRATION_SHA256"]}
    except KeyError as error:
        raise SourceHold("uif_ledger_adapter_registration_required") from error
    settings = _settings(authority, reference)
    supervisor = _path_pin(settings["supervisor"])
    windmill = _fields(settings["windmill"], "base_url script_path token_path runner_receipts runner_uid inspect_version")
    review = _fields(settings["review"], "executable settings")
    review_settings = _review_settings(authority, review["settings"])
    if any(windmill[name] != value for name, value in review_settings["windmill"].items()):
        raise SourceHold("uif_ledger_adapter_review_windmill")
    reviewer = _review_registration(authority, review_settings, _path_pin(review["executable"]),
                                    review["executable"]["sha256"])
    publish_settings = _fields(settings["publisher"], "executable config canonical_readback_askpass")
    client = _WindmillClient(windmill["base_url"], windmill["token_path"])
    publisher_binary = _path_pin(publish_settings["executable"])
    publisher_config = _path_pin(publish_settings["config"])
    config = _record(publish_settings["config"])
    if (config.get("schema") != "uif-native-publisher-config/2"
            or config.get("snapshot_path") != str(authority.snapshot_path)):
        raise SourceHold("uif_ledger_adapter_publisher_config")
    publisher = Publisher(authority.publication_fence, PublisherCommand(
        publisher_binary, publish_settings["executable"]["sha256"], config["closure_sha256"],
        PublisherReceiptPaths(exact_path(config["accepted_root"]), exact_path(config["claim_root"])),
        fixed_args=(str(publisher_config),),
    ), GitReadback(askpass=_path_pin(publish_settings["canonical_readback_askpass"])))
    review_backend = NativeReviewBackend(
        reviewer.executable, executable_sha256=reviewer.executable_sha256,
        evidence_uid=reviewer.evidence_uid, pool_database=reviewer.pool_database,
    )

    def plan_for(key, runtime):
        validation = authority.validation_adapter(
            runtime, client, workspace="foo", script_path=windmill["script_path"],
            runner_receipts=exact_path(windmill["runner_receipts"]),
            runner_uid=windmill["runner_uid"], inspect_version=windmill["inspect_version"],
        )
        review = ReviewAdapter(validation.journal, validation.fence, review_backend, validation=validation)

        def validation_spec(report, outcome):
            values = _fields(_operation_inputs(authority, key, runtime, "validation"),
                             "schema task candidate base head tree branch windmill_job_id runner_job_id "
                             "args_json runner_sha256 capacity_policy_sha256 first_submission")
            if (values["schema"] != "uif-owner-validation/2" or values["task"] != report.task.to_record()
                    or not isinstance(values["args_json"], str)):
                raise SourceHold("uif_ledger_adapter_validation_task")
            binding = StageBinding(report, exact_path(values["candidate"]), values["base"], values["head"],
                                   values["tree"], values["branch"], outcome.worker_receipt_path,
                                   outcome.worker_receipt_sha256)
            return ValidationSpec(binding, binding.key("validation"), values["windmill_job_id"],
                                  values["runner_job_id"], values["args_json"].encode(), values["runner_sha256"],
                                  values["capacity_policy_sha256"], values["first_submission"])

        def review_spec(spec, evidence):
            record = _operation_inputs(authority, key, runtime, "review")
            policy = {"path": record["policy_path"], "sha256": record["policy_sha256"]}
            pinned = authority.ledger_review_record(spec.binding.task, runtime=runtime,
                                                    policy=_path_pin(policy), policy_sha256=policy["sha256"])
            return decode_review_spec(pinned["spec"], snapshot_path=authority.snapshot_path,
                                      invocation_id=_record(policy)["invocation_id"])

        def publication_request(review_specification, validated, reviewed, outcome):
            record = _operation_inputs(authority, key, runtime, "publication_request")
            return PublicationRequest.from_ledger_record(record, snapshot_path=authority.snapshot_path)

        entry, _ = authority.admission._entry(RowKey(key.store_id, key.row_id))
        if entry.get("checkpoint") is not None:
            raise SourceHold("uif_ledger_adapter_checkpoint_unregistered")
        return OperationPlan(validation, review, validation_spec, review_spec, publication_request)

    return CoordinatorBindings(supervisor, settings["supervisor"]["sha256"], publisher, plan_for)
