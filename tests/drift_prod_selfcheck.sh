#!/usr/bin/env bash
# drift_prod_selfcheck.sh — self-check for infra/deploy/check-drift.sh (#360). #360 schedules
# check-drift.sh's original five invariants unchanged (cadence + visibility only), and ADDS a sixth:
# every MDREVIEW_* key docker-compose.prod.yml declares for the `mdreview` service must be present in
# the running container's env. That sixth check exists because the first five would NOT have caught
# the incident that opened #360 -- MDREVIEW_SESSION_TTL_S sitting undeclared on the host for 12 days --
# since none of them reads that key. Runs anywhere: stubs `docker` and `ssh` on PATH, sets HOST=
# (local mode -- the exact invocation the new systemd unit uses) and HOME=<fixture> so the script's
# hardcoded `~/mdreview-deploy/...` reads resolve into a synthetic fixture, never the real host.
#
#   bash tests/drift_prod_selfcheck.sh    # exit 0 = pass
#
# WHY THIS DRIVES THE REAL SCRIPT rather than asserting on its text (same rationale as
# tests/drift_staging_selfcheck.sh / tests/custody_tripwire_selfcheck.py): a comparison that always
# says "ok" passes vacuously on a clean fixture. The only way to know it actually compares anything is
# to plant a divergence and require it to fail — then mutate the comparison itself and require THIS
# selfcheck to notice the detector went silent, by reusing the SAME positive assertion post-mutation
# (the #361 technique) rather than writing a new one that could itself be vacuous.
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
script="$here/infra/deploy/check-drift.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
bin="$tmp/bin"; mkdir -p "$bin"

fail=0
ok(){  printf '  ok   - %s\n' "$1"; }
bad(){ printf '  FAIL - %s\n' "$1"; fail=1; }

# --- ssh stub: proves HOST= truly engages check-drift.sh's local-mode branch. check-drift.sh reads
# `HOST="${HOST-kapture}"` -- the bare `-` only substitutes the ssh default when HOST is UNSET, so
# HOST= (set-but-empty, exactly what mdreview-driftcheck.service's Environment=HOST= sets) must leave
# HOST empty and `run()` must take the `bash -c` branch, never ssh. Get this wrong (e.g. `${HOST:-...}`)
# and the systemd unit ssh's from the host to itself with no key for that hop -- it fails EVERY run
# for a reason that has nothing to do with drift, which is exactly the "nobody reads it" failure #360
# exists to kill. Asserted first, before any fixture content matters.
cat >"$bin/ssh" <<'EOF'
#!/usr/bin/env bash
echo "SSH-CALLED $*" >>"${SSH_MARKER:?}"
exit 1
EOF
chmod +x "$bin/ssh"

# --- docker stub: check-drift.sh only ever calls `docker inspect <container> --format '...Mounts...'`
# or `...Config.Env...`. Anything else is unexpected in this test and fails loudly (exit 3) rather
# than silently defaulting to empty output, which check-drift.sh's own mounts check would read as
# "ok" -- an unreadable stub must never masquerade as a match (the #216 rule).
cat >"$bin/docker" <<'EOF'
#!/usr/bin/env bash
container=""; mode=""
for a in "$@"; do
  case "$a" in
    mdreview|mdreview-oauth2-proxy) container="$a" ;;
  esac
  case "$a" in
    *Config.Env*) mode=env ;;
    *Mounts*)     mode=mounts ;;
  esac
done
f="${DOCKER_FIXTURE_DIR:?DOCKER_FIXTURE_DIR not set}/${mode}-${container}.txt"
if [ -f "$f" ]; then cat "$f"; exit 0; fi
echo "docker stub: no fixture for mode=$mode container=$container (looked for $f)" >&2
exit 3
EOF
chmod +x "$bin/docker"

# --- fixture builder ---------------------------------------------------------------------------------
# build_fixture DIR MODE lays out DIR/home/mdreview-deploy/... (for `~` reads under HOME=DIR/home) and
# DIR/docker/*.txt (read by the docker stub via DOCKER_FIXTURE_DIR=DIR/docker). MODE=clean satisfies
# all five invariants; drift-auth / drift-oauth each break exactly one, via two structurally different
# mechanisms (a docker-inspect env read vs a config-file read), so "fires on divergence" isn't proven
# for only one code path.
build_fixture() {
  local dir="$1" mode="$2"
  rm -rf "$dir"
  mkdir -p "$dir/home/mdreview-deploy/oauth2-proxy" "$dir/docker"

  cat >"$dir/docker/mounts-mdreview.txt" <<'M'
/home/rana/mdreview-deploy
/var/lib/docker/volumes/mdreview-data/_data
M
  cp "$dir/docker/mounts-mdreview.txt" "$dir/docker/mounts-mdreview-oauth2-proxy.txt"

  cat >"$dir/docker/env-mdreview.txt" <<'E'
MDREVIEW_REQUIRE_AUTH=1
MDREVIEW_PUBLIC_BASE=https://app.mdreview.space
MDREVIEW_PROXY_SECRET=test-proxy-secret
MDREVIEW_TOKEN_PEPPER=test-token-pepper
MDREVIEW_SESSION_SECRET=test-session-secret
MDREVIEW_SESSION_TTL_S=2592000
MDREVIEW_DEMO_LOGIN_KEY=
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
E

  cat >"$dir/home/mdreview-deploy/oauth2-proxy/oauth2-proxy.cfg" <<'C'
provider = "oidc"
whitelist_domains = [ "app.mdreview.space" ]
redirect_url = "https://app.mdreview.space/oauth2/callback"
C

  cat >"$dir/home/mdreview-deploy/oauth2-proxy/invited-emails.txt" <<'A'
# invited users
alice@example.com
bob@example.com
A

  case "$mode" in
    clean) : ;;
    drift-auth)
      # The #360 incident's exact shape: a setting the repo's compose declares is silently absent
      # from what the container is actually running.
      grep -v REQUIRE_AUTH "$dir/docker/env-mdreview.txt" >"$dir/docker/env-mdreview.txt.tmp"
      mv "$dir/docker/env-mdreview.txt.tmp" "$dir/docker/env-mdreview.txt"
      ;;
    drift-oauth)
      printf 'provider = "oidc"\nredirect_url = "https://app.mdreview.space/oauth2/callback"\n' \
        >"$dir/home/mdreview-deploy/oauth2-proxy/oauth2-proxy.cfg"
      ;;
    drift-session-ttl)
      # The #360 incident's LITERAL variable: every other key stays present (REQUIRE_AUTH, PUBLIC_BASE,
      # oauth cfg, allowlist all clean) so this isolates the one gap none of checks 1-5 can see.
      grep -v MDREVIEW_SESSION_TTL_S "$dir/docker/env-mdreview.txt" >"$dir/docker/env-mdreview.txt.tmp"
      mv "$dir/docker/env-mdreview.txt.tmp" "$dir/docker/env-mdreview.txt"
      ;;
    *) echo "build_fixture: unknown mode $mode" >&2; exit 9 ;;
  esac
}

# run_check FIXTURE_DIR -> prints check-drift.sh's stdout+stderr; caller reads $? for its exit code.
run_check() {
  local fixture="$1"
  rm -f "$tmp/ssh-called"
  PATH="$bin:$PATH" HOST= HOME="$fixture/home" DOCKER_FIXTURE_DIR="$fixture/docker" \
    SSH_MARKER="$tmp/ssh-called" bash "$script" 2>&1
}

# Reused verbatim across the real-behaviour, mutation, and healed-fixture steps for each invariant --
# the same positive assertion, not a fresh one, is what proves this selfcheck would notice either the
# detector going silent or a genuinely-fixed host going quiet again.
fires_on_auth_drift() {  # $1=stdout $2=exit-code -- check #3 (exact-value REQUIRE_AUTH)
  local out="$1" rc="$2"
  [ "$rc" -ne 0 ] && grep -qF "DRIFT: REQUIRE_AUTH not =1" <<<"$out"
}
fires_on_session_ttl_drift() {  # $1=stdout $2=exit-code -- check #6 (presence sweep), the #360 key itself
  local out="$1" rc="$2"
  [ "$rc" -ne 0 ] && grep -qF "DRIFT: MDREVIEW_SESSION_TTL_S missing from running env" <<<"$out"
}

echo "== 0. HOST= truly avoids ssh (the load-bearing assumption behind mdreview-driftcheck.service) =="
build_fixture "$tmp/clean" clean
out0="$(run_check "$tmp/clean")"; rc0=$?
if [ -s "$tmp/ssh-called" ]; then
  bad "HOST= invoked ssh -- Environment=HOST= in the systemd unit would ssh the host to itself and fail every run"
  sed 's/^/        /' "$tmp/ssh-called"
else
  ok "HOST= stays local (no ssh hop) -- Environment=HOST= in the systemd unit is correct"
fi

echo
echo "== 1. clean fixture: all 6 invariants pass, zero DRIFT lines, exit 0 (stays quiet on an identical pair) =="
if [ "$rc0" -eq 0 ]; then ok "clean fixture exits 0"; else bad "clean fixture exited $rc0 (expected 0)"; fi
if grep -q "DRIFT" <<<"$out0"; then
  bad "clean fixture reported DRIFT"; echo "$out0" | sed 's/^/        /'
else
  ok "clean fixture reports no DRIFT lines"
fi
for line in "no stray ~/mdreview-src deploy dir" \
            "mdreview mounts from mdreview-deploy" \
            "mdreview-oauth2-proxy mounts from mdreview-deploy" \
            "REQUIRE_AUTH=1" "PUBLIC_BASE canonical" \
            "oauth whitelist_domains set" "oauth redirect_url canonical" \
            "allowlist has 2 members" \
            "MDREVIEW_PUBLIC_BASE declared in running env" \
            "MDREVIEW_REQUIRE_AUTH declared in running env" \
            "MDREVIEW_PROXY_SECRET declared in running env" \
            "MDREVIEW_TOKEN_PEPPER declared in running env" \
            "MDREVIEW_SESSION_SECRET declared in running env" \
            "MDREVIEW_SESSION_TTL_S declared in running env"; do
  if grep -qF "$line" <<<"$out0"; then ok "clean fixture: '$line'"
  else bad "clean fixture missing expected ok line: '$line'"; fi
done

echo
echo "== 2. planted divergence (REQUIRE_AUTH absent): fires, and ONLY the checks that reference it -- no false-positive cascade =="
build_fixture "$tmp/drift-auth" drift-auth
out2="$(run_check "$tmp/drift-auth")"; rc2=$?
if fires_on_auth_drift "$out2" "$rc2"; then
  ok "fires (check #3, exact-value): exit non-zero and names REQUIRE_AUTH"
else
  bad "did NOT fire on planted REQUIRE_AUTH drift (check #3)"; echo "$out2" | sed 's/^/        /'
fi
# Deliberate double coverage (documented in check-drift.sh #6's header comment): the presence sweep
# also names MDREVIEW_REQUIRE_AUTH, independently of check #3's exact-value assertion above.
if grep -qF "DRIFT: MDREVIEW_REQUIRE_AUTH missing from running env" <<<"$out2"; then
  ok "fires (check #6, presence sweep): also names MDREVIEW_REQUIRE_AUTH -- intentional double coverage, not a bug"
else
  bad "check #6's presence sweep did NOT also name MDREVIEW_REQUIRE_AUTH"
fi
for line in "no stray ~/mdreview-src deploy dir" "mdreview mounts from mdreview-deploy" \
            "oauth whitelist_domains set" "allowlist has 2 members" \
            "MDREVIEW_SESSION_TTL_S declared in running env" "MDREVIEW_PROXY_SECRET declared in running env"; do
  if grep -qF "$line" <<<"$out2"; then ok "drift-auth fixture: unrelated check still ok ('$line')"
  else bad "drift-auth fixture: unrelated check '$line' wrongly not-ok (false-positive cascade)"; fi
done

echo
echo "== 3. planted divergence (oauth2-proxy.cfg missing whitelist_domains): fires via a DIFFERENT code path (file read, not docker inspect) =="
build_fixture "$tmp/drift-oauth" drift-oauth
out3="$(run_check "$tmp/drift-oauth")"; rc3=$?
if [ "$rc3" -ne 0 ] && grep -qF "DRIFT: oauth whitelist_domains missing" <<<"$out3"; then
  ok "fires: exit non-zero and names oauth whitelist_domains"
else
  bad "did NOT fire on planted oauth whitelist_domains drift"; echo "$out3" | sed 's/^/        /'
fi
if grep -qF "REQUIRE_AUTH=1" <<<"$out3"; then ok "drift-oauth fixture: REQUIRE_AUTH still ok (unrelated check unaffected)"
else bad "drift-oauth fixture: REQUIRE_AUTH wrongly not-ok"; fi
if grep -qF "MDREVIEW_SESSION_TTL_S declared in running env" <<<"$out3"; then
  ok "drift-oauth fixture: check #6's presence sweep still clean (unrelated check unaffected)"
else bad "drift-oauth fixture: MDREVIEW_SESSION_TTL_S wrongly not-ok"; fi

echo
echo "== 4. planted divergence (MDREVIEW_SESSION_TTL_S absent -- the #360 incident's LITERAL variable): fires, and checks 1-5 alone would have missed it =="
build_fixture "$tmp/drift-session-ttl" drift-session-ttl
out4a="$(run_check "$tmp/drift-session-ttl")"; rc4a=$?
if fires_on_session_ttl_drift "$out4a" "$rc4a"; then
  ok "fires: exit non-zero and names MDREVIEW_SESSION_TTL_S"
else
  bad "did NOT fire on planted MDREVIEW_SESSION_TTL_S drift"; echo "$out4a" | sed 's/^/        /'
fi
# The point of this whole addition: prove it's EXACTLY one finding (check #6 on this one key), so
# checks 1-5 -- which is all #360's original scope would have scheduled -- provably say nothing.
ndrift="$(grep -c "DRIFT:" <<<"$out4a")"
if [ "$ndrift" -eq 1 ]; then
  ok "exactly one DRIFT line -- checks 1-5 all still report ok, confirming they cannot see this key"
else
  bad "expected exactly 1 DRIFT line, got $ndrift -- either a false-positive cascade or the wrong finding"
  echo "$out4a" | sed 's/^/        /'
fi

echo
echo "== 5. mutation test: neuter check-drift.sh's REQUIRE_AUTH comparison (check #3), prove THIS selfcheck notices the detector going silent =="
ANCHOR='grep -qx "MDREVIEW_REQUIRE_AUTH=1"'
if ! grep -qF "$ANCHOR" "$script"; then
  bad "mutation harness: expected anchor not found in check-drift.sh (script changed -- update this selfcheck's anchor)"
else
  mutated="$tmp/mutated-check-drift.sh"
  sed "s/${ANCHOR//\//\\/}/grep -q \"\"/" "$script" >"$mutated"
  chmod +x "$mutated"
  real_script="$script"; script="$mutated"
  out5="$(run_check "$tmp/drift-auth")"; rc5=$?
  script="$real_script"
  if fires_on_auth_drift "$out5" "$rc5"; then
    bad "MUTATION NOT CAUGHT -- neutered comparison still reported drift (this selfcheck's patch is wrong, not a real result)"
    echo "$out5" | sed 's/^/        /'
  else
    ok "MUTATION CAUGHT: the SAME 'fires on planted drift' assertion used in step 2 now evaluates false against the neutered detector -- this selfcheck would notice check-drift.sh's comparison going silent"
  fi
fi

echo
echo "== 6. drift-auth fixture healed in place: the SAME check-#3 assertion correctly flips back to quiet (it isn't a tautology that always reports drift) =="
build_fixture "$tmp/drift-auth" clean   # rebuild the FULL 6-key clean env, not just the 2 keys check #3 cares about
out6="$(run_check "$tmp/drift-auth")"; rc6=$?
if fires_on_auth_drift "$out6" "$rc6"; then
  bad "healed fixture still reads as drifted -- the assertion is not actually discriminating"
  echo "$out6" | sed 's/^/        /'
else
  ok "healed fixture is quiet again (exit $rc6, no REQUIRE_AUTH DRIFT line) -- same assertion, real signal"
fi

echo
echo "== 7. mutation test: neuter check-drift.sh's #6 presence sweep, prove THIS selfcheck notices IT going silent =="
ANCHOR6='grep -qE "^${k}="'
if ! grep -qF "$ANCHOR6" "$script"; then
  bad "mutation harness: expected anchor not found in check-drift.sh (script changed -- update this selfcheck's anchor)"
else
  mutated6="$tmp/mutated-check-drift-6.sh"
  sed "s/${ANCHOR6//\//\\/}/grep -q \"\"/" "$script" >"$mutated6"
  chmod +x "$mutated6"
  real_script="$script"; script="$mutated6"
  out7="$(run_check "$tmp/drift-session-ttl")"; rc7=$?
  script="$real_script"
  if fires_on_session_ttl_drift "$out7" "$rc7"; then
    bad "MUTATION NOT CAUGHT -- neutered presence sweep still reported drift (this selfcheck's patch is wrong, not a real result)"
    echo "$out7" | sed 's/^/        /'
  else
    ok "MUTATION CAUGHT: the SAME 'fires on planted MDREVIEW_SESSION_TTL_S drift' assertion used in step 4 now evaluates false against the neutered detector -- this selfcheck would notice check #6 going silent"
  fi
fi

echo
echo "== 8. drift-session-ttl fixture healed in place: the SAME check-#6 assertion correctly flips back to quiet =="
build_fixture "$tmp/drift-session-ttl" clean
out8="$(run_check "$tmp/drift-session-ttl")"; rc8=$?
if fires_on_session_ttl_drift "$out8" "$rc8"; then
  bad "healed fixture still reads as drifted -- the assertion is not actually discriminating"
  echo "$out8" | sed 's/^/        /'
else
  ok "healed fixture is quiet again (exit $rc8, no MDREVIEW_SESSION_TTL_S DRIFT line) -- same assertion, real signal"
fi

echo
echo "== 9. sync guard: check-drift.sh's embedded MDREVIEW_* key list matches docker-compose.prod.yml's (a silent mismatch here reproduces #360 one level up) =="
compose_file="$here/infra/deploy/docker-compose.prod.yml"
extract_compose_keys() {  # $1 = compose file path
  grep -oE '^[[:space:]]+MDREVIEW_[A-Z0-9_]+:' "$1" | sed -E 's/^[[:space:]]+//; s/:$//' | sort -u
}
extract_script_keys() {  # $1 = check-drift.sh path
  grep -oE '^EXPECTED_MDREVIEW_KEYS="[^"]*"' "$1" | sed -E 's/^EXPECTED_MDREVIEW_KEYS="//; s/"$//' \
    | tr ' ' '\n' | sort -u
}
compose_keys="$(extract_compose_keys "$compose_file")"
script_keys="$(extract_script_keys "$script")"
if [ -z "$compose_keys" ] || [ -z "$script_keys" ]; then
  bad "sync guard: could not extract keys (compose=${compose_keys:-<empty>} script=${script_keys:-<empty>}) -- unreadable is not the same as matching"
elif [ "$compose_keys" = "$script_keys" ]; then
  ok "check-drift.sh's embedded list matches docker-compose.prod.yml exactly ($(wc -l <<<"$compose_keys" | tr -d ' ') keys)"
else
  bad "check-drift.sh's embedded MDREVIEW_* key list has drifted from docker-compose.prod.yml -- update EXPECTED_MDREVIEW_KEYS"
  echo "        compose declares: $(tr '\n' ' ' <<<"$compose_keys")"
  echo "        script expects  : $(tr '\n' ' ' <<<"$script_keys")"
fi

echo
echo "== 10. sync guard self-test: a corrupted COPY of the compose file (never the real one) must fail the same guard =="
corrupt_compose="$tmp/docker-compose.prod.corrupt.yml"
grep -v MDREVIEW_SESSION_TTL_S "$compose_file" >"$corrupt_compose"
corrupt_keys="$(extract_compose_keys "$corrupt_compose")"
if [ "$corrupt_keys" != "$script_keys" ]; then
  ok "MUTATION CAUGHT: the SAME comparison used in step 9 correctly flags a corrupted compose copy as mismatched"
else
  bad "sync guard did not notice a key removed from a corrupted compose copy"
fi

echo
[ "$fail" -eq 0 ] && echo "drift-prod selfcheck: all clear" || echo "drift-prod selfcheck FAILED"
exit "$fail"
