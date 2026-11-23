# Stokowski AI Gateway routing

The native Stokowski owner supplies the account map and Free-account credential
resolver when constructing the Workers AI client. To enable the OffMenu gateway,
map the OffMenu account ID to the existing gateway name in that owner binding.

```python
client = WorkersAIClient(
    router,
    free_account_token,
    gateways={accounts["OffMenu"]: "stokowski"},
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
and response collection disabled. The OffMenu gateway's $0.11 rolling 24-hour
rule is a secondary control. Stokowski still caps new work at 7,500 neurons per
UTC day and work in progress at 10,000. The Workers Free plan supplies the
provider-enforced cutoff.

The native owner must pass the map from its protected launcher configuration.
An installed wheel or saved gateway by itself does not make native Stokowski
send requests through the gateway. Live qualification should record the gateway
receipt, account assignment, neuron usage, and native attempt result.
