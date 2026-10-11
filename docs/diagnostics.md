# Diagnostics

The image includes `doctor` and `verify`; no checkout or extra packages are needed
on the server. Both commands are read-only and return `0` on success, `1` on failed
checks, and `2` for invalid command arguments. Warnings alone do not fail a check.
Neither command prints subscription URLs, connection keys, routing contents or
raw exception messages.

## Check configuration

Run with the same user, environment and mounts as the service:

```bash
podman exec systemd-submerge python -m submerge doctor
podman exec systemd-submerge python -m submerge doctor --require-routing happ
```

`doctor` reports configured file paths and checks source/rewrite/metadata JSON with
the service's parsers. It checks that templates are readable and nonempty, the
Mihomo template contains `${PROVIDER_URL}`, and translations load correctly.
It does not validate JavaScript, YAML semantics or client-specific routing rules.
No subscriptions, DNS rewrites or geofiles are fetched.

Missing optional metadata produces a warning. `--require-routing happ` makes an
absent Happ profile fail the command; repeat the option for `v2raytun` to require
both. This flag affects only the check, not service startup. Invalid existing
files and missing explicitly configured rewrite files fail even without this flag.

Files are read fresh from disk, so invalid edits fail even if the running service
retains an older valid configuration. The command reports current file availability,
not the configuration cached in an existing process.

With the supplied Quadlet, `/etc/submerge` on the host is mounted at `/config` in
the container. A metadata file left in `/opt/submerge` is not visible there.

## Verify a public subscription after updating

Use the public HTTPS subscription URL to include nginx and TLS in the check.
This sends one real subscription request with the selected client's User-Agent.
It preserves the URL and query exactly; it does not silently force `format=happ`.

Interactive use prompts for the URL without echoing it:

```bash
podman exec -it systemd-submerge python -m submerge verify --require-routing
```

For a root shell or automation, pass the URL on stdin. This Bash example avoids
putting the token in shell history or process arguments:

```bash
read -rsp 'Subscription URL: ' subscription_url
printf '\n'
printf '%s\n' "$subscription_url" |
  podman exec -i systemd-submerge python -m submerge verify \
    --url-stdin --client happ --require-routing --min-links 4
unset subscription_url
```

Choose `--min-links` for your deployment; the default is one. Use `--client v2raytun`
for its routing format or `--client generic` for raw base64 only. Happ is the default.
`--require-routing` requires a valid named JSON profile in the `Routing` header;
for Happ it also requires `Routing-Enable: 1` or `true`. Without that flag, missing
routing is a warning, but a present malformed profile still fails.

The check requires HTTP 200, a strict base64 UTF-8 URI list, the requested minimum
connection count, and a matching `Content-Length` when supplied. HTML error pages,
plain text, truncated responses and malformed routing fail. Downloads are limited
to 8 MiB. `--timeout 30` sets the network operation timeout in seconds; it is not
a wall-clock deadline for the whole download. HTTPS certificate validation stays on.

This confirms the delivered format, not whether every upstream contributed: use
`ALLOW_PARTIAL=0` if all sources must succeed. Check actual import, connection and
geofile downloads in the client. For external reachability, also run `verify`
from a workstation (`uv run --locked python -m submerge verify --require-routing`).
