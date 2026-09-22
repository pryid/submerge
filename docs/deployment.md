# Deployment

## Publish the image

The workflow builds and tests `linux/amd64` and `linux/arm64` on native runners.
Red Hat Actions use Buildah to build and Podman to publish a single multiarch
manifest. GitHub's `GITHUB_TOKEN` has `packages: write` only in the publication job;
no custom publishing secret is required. Pull requests only validate.

1. Commit the project changes and create an unused tag: `git tag v1.0.0`.
2. Push the branch and tag together: `git push --atomic origin main v1.0.0`.
3. Wait for tests, both architecture jobs and publication to pass.
4. Set the GHCR package visibility to **Public** for anonymous server pulls.

The image is named `ghcr.io/<repository-owner>/submerge`. Forks must update the
owner in [submerge.container](../deploy/submerge.container). Private packages
require persistent Podman credentials for the service and auto-update user.
Keep release tags immutable. OCI labels record the repository and commit.
The browser page shows the image's short commit SHA at the bottom; hovering over
it reveals the full SHA. CI embeds it during the build, so no server setting is needed.

Automatic runs happen only for pull requests and release tags. Pushing `main`
does not start a workflow, so pushing the branch and one release tag together
starts one release run. PR changes confined to `README.md` and `docs/` run code
checks/tests but skip image jobs. Release tags and manual runs always check images.

For manual publication, open **Actions → Test and publish image → Run workflow**,
select `main` and enable **publish**. This publishes `main` and `sha-<commit>`
without updating `stable`. To publish
an existing release tag, select that tag as the workflow ref (or use
`gh workflow run ci.yml --ref vX.Y.Z -f publish=true`). The selected ref must
already contain the new workflow. Leave **publish** unchecked for validation only;
feature branches cannot publish. Prefer a new release tag for production updates.

Only the highest stable version among the current remote Git tags may update
`stable`; version comparison is numeric (`v1.10.0` is newer than `v1.9.0`). Older
versions still receive their version/commit tags. Prereleases never update or
block `stable`. Invalid versions, including `+build` metadata unsupported in
container tags, fail before building. A removed or moved release tag fails before
publication. The `main` alias updates only if the commit is still the branch head.

Publications share a queue and do not cancel each other. The release policy is
checked inside this queue using fresh remote refs. If the newest release fails
tests, `stable` stays on the previous published release: fix/retry the newest
release or publish a higher version. Keep pushed release tags unchanged and do
not delete them. Check the publication job's summary for tags and digest.

Images cached for the same commit/architecture are reused and tested again on
release runs. Cache misses rebuild normally; simultaneous manual/tag runs may both
build. Native ARM runners require repository/account support for
`ubuntu-24.04-arm`. The server Quadlet and config paths work with either architecture.

## Server files

The Quadlet assumes nginx and Submerge share an existing `main.pod`. Both use the
pod's loopback interface; no extra `PublishPort` is needed.

```text
/etc/containers/systemd/submerge.container
/etc/submerge/sub_bases.json          # required
/etc/submerge/link_rewrites.json      # optional
/etc/submerge/sub_metadata.json       # optional
/etc/submerge/mihomo.yaml             # optional custom template
```

Only Podman, Quadlet/systemd and the existing nginx setup are needed on the server.
The image includes Python, dependencies and default assets. It runs as UID/GID
65532, with a read-only root filesystem and `/etc/submerge` mounted read-only at `/config`.

Create `/etc/submerge` and place your edited [example configs](../examples/) there.
For a rootful pod, a root-owned directory/files with group 65532 and modes
0750/0640 provide access without making configs world-readable. On SELinux systems,
replacement files must retain the container label; edit in place or preserve the
existing labels when replacing files.

Copy [the Quadlet](../deploy/submerge.container) to
`/etc/containers/systemd/submerge.container`. Uncomment the rewrite environment
line if that file is used, and the Mihomo line if supplying a custom template.
Other settings are in the [configuration reference](configuration.md).

```bash
sudo podman pull ghcr.io/pryid/submerge:stable
sudo systemctl daemon-reload
sudo systemctl start submerge.service
sudo systemctl enable --now podman-auto-update.timer
sudo systemctl status submerge.service
sudo journalctl -u submerge.service -n 50
```

Do not enable the generated `submerge.service`: the Quadlet's `[Install]` section
controls startup. The timer checks the registry on its schedule, not immediately
after publication.

## nginx

Use [nginx-submerge.conf](../deploy/nginx-submerge.conf) **inside the existing
`server` block**. It is not a complete `http`-level config. Preserve the server's
TLS, rate limits and other locations.

When adapting an existing location, preserve the query string:

```nginx
proxy_pass http://127.0.0.1:18080/sub/$1$is_args$args;
```

Without `$is_args$args`, format overrides are lost because the URI contains a
variable. The supplied snippet has its own named 404 handler.
Find nginx's container name with `podman ps`, then validate and reload inside it:

```bash
sudo podman exec <nginx-container> nginx -t
sudo podman exec <nginx-container> nginx -s reload
```

## Migrate from `/opt/submerge`

1. Publish and pull the first release before replacing the running service.
2. Back up the old Quadlet. Copy existing JSON configs from `/opt/submerge` to
   `/etc/submerge`, preserving contents and setting readable permissions.
3. Copy custom Mihomo/metadata files if used. Update environment paths to `/config/...`.
4. Install the new Quadlet and retain deployment-specific environment values.
   Enable the rewrite file line if rewrites were enabled previously.
5. Correct nginx's query forwarding, validate and reload nginx.
6. Run `systemctl daemon-reload` and `systemctl restart submerge.service`.
   Verify public formats before removing the old files.

JSON formats are unchanged. The pod and network remain managed by your existing deployment.

## Updates and rollback

For an immediate registry check or update:

```bash
sudo podman auto-update --dry-run
sudo podman auto-update
```

These commands apply to **all** eligible containers, including nginx if it uses
`AutoUpdate=registry`. Updates briefly interrupt requests while restarting the
service. A successful systemd restart is not a client compatibility check; CI
tests HTTP behavior before publication.

For rollback, set `Image=` to a previously recorded digest:

```ini
Image=ghcr.io/<owner>/submerge@sha256:<digest>
```

Remove `AutoUpdate=registry` while pinned, then reload systemd and restart Submerge.
Restore `stable` and auto-update when ready to follow releases.

## Verify

Open `https://example.com/sub-merge/<id>` and check a real client. From a workstation
checkout, the live smoke script checks raw, HTML and Mihomo responses:

```bash
./scripts/test_formats.sh https://example.com/sub-merge/<id>
```

The internal `/healthz` endpoint serves GET/HEAD without fetching upstreams. It
checks the HTTP process, not subscription validity or upstream availability.
