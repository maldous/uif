# Stokowski AI Gateway routing

The native Stokowski owner supplies the account map and Free-account credential
resolver when constructing the Workers AI client. Gateway mappings are optional
and keyed by Cloudflare account ID. Add a mapping only for an account whose
requests should go through a named gateway.

```python
client = WorkersAIClient(
    router,
    free_account_token,
    gateways={account_id: gateway_name},
)
```

Each job reserves capacity on its assigned account before the request. A mapped
account uses the account-scoped Chat Completions endpoint with the named gateway.
The client asks for one provider attempt and checks for a gateway receipt and
the selected model before settling provider-reported neuron usage. A gateway
refusal or missing receipt leaves the reservation in place. The client makes no
direct endpoint retry. Accounts without a gateway mapping use the direct
Workers AI endpoint.

Keep standard Workers AI billing and authenticated gateway access. Keep prompt
and response collection disabled. Any gateway spend rule is a secondary control. Stokowski still caps new work at 7,500 neurons per
UTC day and work in progress at 10,000. The Workers Free plan supplies the
provider-enforced cutoff.

When gateway routing is enabled, the native owner passes the map from its
protected launcher configuration.
An installed wheel or saved gateway by itself does not make native Stokowski
send requests through the gateway. Live qualification should record the gateway
receipt, account assignment, neuron usage, and native attempt result.

## Native provider selection

Set `provider_mode` in the protected owner configuration and restart the
controller after the active work has settled. The default `cloudflare` runs
Workers AI exclusively. `codex` selects the existing native Codex runner.
`both` selects Codex for an eligible author turn when its project has a fresh
operator readiness attestation and the Codex executable is present. Workers AI
handles the turn when either condition fails. Independent Workers AI reviews
still use the separately admitted review account.

The `codex_ready_until` mapping accepts a UTC expiry per project. The operator
sets it after verifying Codex capability and capacity. No model call checks
readiness. In `codex` mode, the native workflow supplies the Codex model.
In `both` mode, a launched native Codex operation retains its original
settlement path.

## Paid account using the included allocation

An optional `Paid` account can use its included Workers AI allocation when
every Workers AI caller for the account shares Stokowski's ledger. The
operator reconciles existing usage before setting `paid_workers_ai_exclusive`
and supplying the protected paid credential. Each request still reserves its
maximum usage before dispatch. The same 7,500-neuron admission cap and
10,000-neuron hard cap apply per UTC day, including concurrent requests and
unsettled usage. A pending Paid request keeps its reservation across midnight.
A request that settles on a later day also counts against that day's headroom.

An authenticated AI Gateway can add a secondary spend limit. Its limit uses
completed requests, so the account-wide ledger provides the concurrency
control. Clear the exclusivity flag before allowing a separate inference
caller on the account.
