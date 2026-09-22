# HWID integration proposal

This is a design note based on the examined 3x-ui 3.8.5 checkout. HWID forwarding
is not implemented in Submerge; `fetch()` currently sends its own User-Agent.

## What the panel does

With an enabled device limit, the subscription endpoint reads `X-HWID`,
`User-Agent`, `X-Device-OS`, `X-Ver-OS` and `X-Device-Model`. It stores a hash of
the HWID and registers a new device when a slot is available. Known devices can
refresh even when the limit is full. Missing/invalid HWID or a new device beyond
the limit receives HTTP 404 and diagnostic `X-Hwid-*` response headers.

This gate controls fetching subscriptions. It is not a cryptographic device
identity or a count of simultaneous VPN connections: a caller supplies the ID,
and downloaded configurations can still be copied.

## Recommended first step

Add opt-in forwarding of the listed device headers to configured sources, with
bounded lengths and no raw HWID in logs. Keep enforcement in each panel and
expose meaningful diagnostics when a source denies the request. Preserve explicit
upstream format selection: forwarding a client's User-Agent may cause 3x-ui to
return JSON or YAML instead of the expected URI list.

Define a policy for mixed results. Partial mode can serve nodes from panels that
accepted the device, with a clear partial-status indication; strict mode should
reject the whole response on a HWID denial. Do not treat `X-Hwid-Limit` alone as a
rejection: an already registered device can receive it on a successful response.
Any future response cache must include the device identity and format context.

## Multiple panels and the browser

Each panel has its own database and device slots. Forwarding one HWID to four
panels can register it four times, once per panel; it does not create a shared
limit. A global limit needs a designated authority or shared state, which adds
server storage and operational responsibilities.

Browsers usually provide no HWID. Do not synthesize a browser ID or consume a
slot just to display status. The examined panel offers read-only
`<subscription-url>/hwid-status` and `?format=info`; Submerge could use these for
a status-only page. Configuration exports need a separate, explicit access
policy; substituting the panel's HTML endpoint to bypass a denied raw request
would undermine the device limit.

A first implementation can remain stateless: forward device metadata, handle
per-panel decisions, and show device-slot status where available. Centralized
registration and a global quota should be a separate feature.
