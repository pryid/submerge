# Diagnostics

The image includes `doctor`; no checkout or extra packages are needed on the server.
The command is read-only and returns `0` on success, `1` on failed checks, and `2`
for invalid command arguments. Warnings alone do not fail a check. It does not
print upstream URLs, routing contents or raw exception messages.

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
