#!/usr/bin/env bash
# check-drift.sh — detect deploy drift between the LIVE Kapture host and this repo's infra/deploy/ (issue #86).
#
# The class of incident #86 exists to kill: the live config is hand-edited host state that silently
# diverges from the repo, so a redeploy or container-recreate regresses domains/auth/membership. Run
# this after any deploy change (and periodically) — it asserts the invariants that broke sign-in three
# times in one day. Exit 0 = clean, 1 = drift.
#
#   ./infra/deploy/check-drift.sh            # from a machine with `ssh kapture`
#   HOST= ./infra/deploy/check-drift.sh      # on the host itself (no ssh hop)
set -uo pipefail
HOST="${HOST-kapture}"
run(){ if [ -n "$HOST" ]; then ssh -o ConnectTimeout=15 "$HOST" "$@"; else bash -c "$@"; fi; }
fail=0; drift(){ echo "  DRIFT: $*"; fail=1; }; ok(){ echo "  ok   : $*"; }

echo "== mdreview deploy-drift check (host=${HOST:-local}) =="

# 1. exactly ONE deploy dir — the orphaned ~/mdreview-src must be archived so it can't be run/edited
if run "test -d ~/mdreview-src/infra/deploy" 2>/dev/null; then
  drift "~/mdreview-src/infra/deploy still present — archive it (~/mdreview-src.RETIRED-<date>)"
else ok "no stray ~/mdreview-src deploy dir"; fi

# 2. both containers mount their config/data from the single live dir (~/mdreview-deploy)
for c in mdreview mdreview-oauth2-proxy; do
  bad=$(run "docker inspect $c --format '{{range .Mounts}}{{.Source}}{{\"\\n\"}}{{end}}'" 2>/dev/null \
        | grep -vE 'mdreview-deploy|/var/lib/docker/volumes' | grep -E 'mdreview' )
  [ -z "$bad" ] && ok "$c mounts from mdreview-deploy" || drift "$c mounts off mdreview-deploy: $bad"
done

# 3. running app: Phase-1 auth ON + the single canonical domain
env=$(run "docker inspect mdreview --format '{{range .Config.Env}}{{println .}}{{end}}'" 2>/dev/null)
echo "$env" | grep -qx "MDREVIEW_REQUIRE_AUTH=1" && ok "REQUIRE_AUTH=1" || drift "REQUIRE_AUTH not =1 (Phase-1 auth OFF)"
echo "$env" | grep -qx "MDREVIEW_PUBLIC_BASE=https://app.mdreview.space" && ok "PUBLIC_BASE canonical" || drift "PUBLIC_BASE not app.mdreview.space"

# 4. live oauth2-proxy.cfg carries the whitelist + canonical redirect (the incident-2 fix)
cfg=$(run "cat ~/mdreview-deploy/oauth2-proxy/oauth2-proxy.cfg" 2>/dev/null)
echo "$cfg" | grep -qE 'whitelist_domains *= *\[ *"app.mdreview.space" *\]' && ok "oauth whitelist_domains set" || drift "oauth whitelist_domains missing (sign-in will loop)"
echo "$cfg" | grep -qF 'redirect_url = "https://app.mdreview.space/oauth2/callback"' && ok "oauth redirect_url canonical" || drift "oauth redirect_url not canonical"

# 5. the live allowlist has at least the invited users (incident-3 was a dropped invite); names not
#    hardcoded here (PII / host-managed) — assert count >= 2, cross-check membership by hand if it trips
n=$(run "grep -vcE '^#|^[[:space:]]*$' ~/mdreview-deploy/oauth2-proxy/invited-emails.txt" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 2 ] && ok "allowlist has $n members (>=2 invited users)" || drift "allowlist has only ${n:-0} member(s) — an invite may have been dropped"

# 6. every MDREVIEW_* key the repo's docker-compose.prod.yml declares for the `mdreview` service must
#    be PRESENT in the running container's env (#360: MDREVIEW_SESSION_TTL_S sat undeclared on the
#    host for 12 days because the host's copy of that file was stale — auto-update.sh recreates FROM
#    the host's file, so a repo-side fix to it is inert until synced, and was inert silently). Presence
#    only, not value: several of these are per-host secrets with no reproducible expected value here.
#    An explicitly-set-EMPTY value ("KEY=" in `docker inspect`) still counts as present — the compose
#    file declared it and the container has the slot; only a key with NO line at all is drift. (Checks
#    3 above additionally assert an exact VALUE for REQUIRE_AUTH/PUBLIC_BASE; this sweep is presence
#    only and deliberately re-covers those two as well, so "declared but absent" is never a gap between
#    the two checks.)
#    The list is embedded here rather than read from the repo (mirrors REQUIRE_AUTH/PUBLIC_BASE above)
#    because the host does not check out the repo — check #1 exists to enforce exactly that, so this
#    script cannot read infra/deploy/docker-compose.prod.yml live. Kept in sync by
#    tests/drift_prod_selfcheck.sh, which parses the repo's actual compose file and fails if its
#    MDREVIEW_* key set no longer matches this list — a silent rot here would reproduce #360 one level
#    up. Kept on ONE line (not wrapped) so that selfcheck's sync guard can extract it with a plain grep.
EXPECTED_MDREVIEW_KEYS="MDREVIEW_PUBLIC_BASE MDREVIEW_REQUIRE_AUTH MDREVIEW_PROXY_SECRET MDREVIEW_TOKEN_PEPPER MDREVIEW_SESSION_SECRET MDREVIEW_SESSION_TTL_S MDREVIEW_DEMO_LOGIN_KEY"
for k in $EXPECTED_MDREVIEW_KEYS; do
  echo "$env" | grep -qE "^${k}=" && ok "$k declared in running env" \
    || drift "$k missing from running env (compose declares it, container does not have it)"
done

echo "== $( [ $fail -eq 0 ] && echo 'CLEAN: live matches the repo intent' || echo 'DRIFT DETECTED — reconcile before any redeploy' ) =="
exit $fail
