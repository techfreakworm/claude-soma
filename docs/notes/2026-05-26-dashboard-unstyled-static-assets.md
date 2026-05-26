# Dashboard renders unstyled — Next.js standalone static assets not served

2026-05-26. `/admin` (and the whole dashboard) rendered as raw unstyled HTML on
desktop -- Times serif, run-together nav, no layout -- while mobile looked fine.

## Diagnosis (not a responsive bug)

Unstyled = the CSS bundle isn't loading at all. Confirmed it's a static-asset
serving failure, not CSS/layout:

```
# the standalone server is up
curl -s -o /dev/null -w '%{http_code}\n' https://soma.mayankgupta.in/          # 200
# but every built asset 404s
curl -s -o /dev/null -w '%{http_code}\n' https://soma.mayankgupta.in/_next/static/chunks/<hash>.css   # 404
```

On disk the assets existed at `/opt/claude-soma/frontend/.next/static/...` but
`/opt/claude-soma/frontend/.next/standalone/.next/static/` and
`.../standalone/public/` did **not** exist. "Mobile fine" was just a stale
browser cache serving a previously-working build.

## Root cause

`next.config.mjs` uses `output: "standalone"`. That emits a minimal
`.next/standalone/server.js` but does NOT copy `.next/static` or `public/` next
to it. The standalone server serves `/_next/static/*` from
`<server.js dir>/.next/static`, so without the copy every CSS/JS/font 404s and
the page renders with browser defaults.

`scripts/build_frontend.sh` was written to do that copy (commit 27e5f8b), but it
was **never wired into the deploy**: `scripts/deploy.sh` rsynced source
(excluding `.next`) and installed Python deps, but never built the frontend.
NEXT.md's manual build said `pnpm build` -- a bare build that also skips the
copy. So whatever last ran `pnpm build` left a standalone tree with no static.

## Fix

- `scripts/deploy.sh`: build the frontend on the server via
  `build_frontend.sh`, then restart `claude-soma-frontend.service` (so the new
  build's asset hashes are the ones served).
- `scripts/build_frontend.sh`: made the copy rebuild-safe -- `rm -rf` the
  targets then `cp -r`, because `cp -rf .next/static .next/standalone/.next/static`
  into an existing dir nests one level too deep (`.../static/static`) and
  re-404s on the second deploy.
- NEXT.md: manual build step now calls `build_frontend.sh`, not bare `pnpm build`.

## Verified

Built locally with the fixed `build_frontend.sh`, served
`.next/standalone/server.js`, and confirmed the exact assets that 404 in
production return 200 with correct Content-Type (`text/css`,
`application/javascript`). A live before/after screenshot needs a deploy (out of
scope here -- do NOT self-deploy); the 404->200 on a real served build is the
proof the root cause is fixed.
