#!/usr/bin/env bash
# gate0.sh: the whole phase-0 proof from nothing, then the stack down again. Dummy data only.
#   DEV_DATA (default /var/tmp/machiya-login/dev-data): the throwaway CA, the secrets, the snapshot, results
#   SHOTS    (default /var/tmp/machiya-login/shots): the screenshots
#   PYTHON   a Python with Playwright (Chromium and WebKit installed)
# Steps: a throwaway CA and *.machiya.test certificate; random secrets; the helper image; Hister with user handling off
# and dummy pool data (design §8 step 1: the numbers written down, then a snapshot); the owner made with
# `hister create-user` in a TTY; rules and history moved in SQL; the stack up with user handling on; the documents
# moved with `hister update`; the move verified; the Playwright checks (gate0.py); `compose down -v`.
# KEEP=1 leaves the stack up. Needs a podman API socket for compose (DOCKER_HOST); one is started if it isn't set.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
export DEV_DATA="${DEV_DATA:-/var/tmp/machiya-login/dev-data}" SHOTS="${SHOTS:-/var/tmp/machiya-login/shots}"
PYTHON="${PYTHON:-$HOME/data/venvs/shots/bin/python}"
HISTER_IMAGE=ghcr.io/asciimoo/hister:v0.20.0
PY_IMAGE=public.ecr.aws/docker/library/python:3.13-slim
NET=machiya-login_default
cd "$here"
if [[ -z ${DOCKER_HOST:-} ]]; then
    sock="$DEV_DATA/podman.sock"; mkdir -p "$DEV_DATA"
    podman system service --time=0 "unix://$sock" & svc=$!
    trap 'kill $svc 2>/dev/null || true' EXIT
    export DOCKER_HOST="unix://$sock"; sleep 2
fi
compose() { podman compose "$@" 2>&1 | grep -v "Executing external compose provider" || true; }
step() { printf '\n== %s\n' "$*"; }

step "clean start"
compose down -v >/dev/null
mkdir -p "$DEV_DATA"/{certs,secrets,idp} "$SHOTS"
chmod 700 "$DEV_DATA/secrets"; chmod 755 "$DEV_DATA/idp"
( umask 077
  python3 -c "import secrets; print(secrets.token_urlsafe(18))" > "$DEV_DATA/secrets/owner-password"
  python3 -c "import secrets; print(secrets.token_urlsafe(24))" > "$DEV_DATA/idp/oidc-client-secret"
  printf 'HISTER__SERVER__OAUTH__OIDC__CLIENT_SECRET=%s\n' "$(cat "$DEV_DATA/idp/oidc-client-secret")" \
      > "$DEV_DATA/secrets/hister.env" )
chmod 644 "$DEV_DATA/idp/oidc-client-secret"          # read by the stub IdP's container user (dev throwaway)
if [[ ! -s $DEV_DATA/certs/tls.crt ]]; then
    ( cd "$DEV_DATA/certs"
      openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 30 \
          -subj "/CN=machiya-login throwaway dev CA" 2>/dev/null
      openssl req -newkey rsa:2048 -nodes -keyout tls.key -out tls.csr -subj "/CN=*.machiya.test" 2>/dev/null
      printf "subjectAltName=DNS:*.machiya.test,DNS:machiya.test\nextendedKeyUsage=serverAuth\n" > ext.cnf
      openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out tls.crt -days 30 \
          -extfile ext.cnf 2>/dev/null
      chmod 644 tls.crt tls.key )
fi

step "the helper image"
podman build -q -t localhost/hister-login:dev .. >/dev/null

step "§8 step 1: Hister without users, dummy pool data, the numbers, a snapshot"
HISTER_USER_HANDLING=false compose up -d hister >/dev/null
sleep 5
podman run --rm --network $NET -v ./move.py:/move.py:ro -v "$DEV_DATA:/out" $PY_IMAGE \
    sh -c "python3 /move.py seed && python3 /move.py numbers /out/before.json"
compose stop hister >/dev/null
podman volume export machiya-login_hister-data > "$DEV_DATA/hister-pre-users-snapshot.tar"

step "§8 step 2: the owner (create-user in a TTY), its token to a file"
oneoff=(podman run --rm --env-file "$DEV_DATA/secrets/hister.env" -v machiya-login_hister-data:/hister/data
        -v ./hister-config.yml:/hister/config.yml:ro -e HISTER_CONFIG=/hister/config.yml -e HISTER__APP__USER_HANDLING=true)
python3 tty_create_user.py "$DEV_DATA/secrets/owner-password" "${oneoff[@]}" -it $HISTER_IMAGE create-user owner --admin
( umask 077
  "${oneoff[@]}" $HISTER_IMAGE show-user owner --token 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' \
      | awk -F': *' '/^Token/{print $2}' | tr -d ' \r' > "$DEV_DATA/secrets/owner-token"
  printf 'HISTER__APP__ACCESS_TOKEN=%s\n' "$(cat "$DEV_DATA/secrets/owner-token")" > "$DEV_DATA/secrets/owner-token.env" )
[[ -s $DEV_DATA/secrets/owner-token ]] || { echo "no owner token" >&2; exit 1; }

step "§8 steps 3-4: rules and history to the owner (Hister stopped)"
podman run --rm -v machiya-login_hister-data:/hister/data -v ./move.py:/move.py:ro $PY_IMAGE python3 /move.py sql owner

step "§8 step 5: the stack, with user handling on and the OIDC config"
HISTER_USER_HANDLING=true compose up -d >/dev/null
for _ in $(seq 30); do
    curl -sf --cacert "$DEV_DATA/certs/ca.crt" --resolve hister.machiya.test:19043:127.0.0.1 \
        https://hister.machiya.test:19043/machiya/healthz 2>/dev/null | grep -q '"hister": "ok"' && break
    sleep 2
done

step "§8 step 6: the documents (hister update, the token from an env file)"
cli=(podman run --rm --network $NET --env-file "$DEV_DATA/secrets/hister.env" --env-file "$DEV_DATA/secrets/owner-token.env"
     -v ./hister-config.yml:/hister/config.yml:ro -e HISTER_CONFIG=/hister/config.yml $HISTER_IMAGE -u http://hister:4433)
"${cli[@]}" update "user_id:0" --user-id 1 --dry 2>&1 | grep -v tui.yaml | sed 's/\x1b\[[0-9;]*m//g'
"${cli[@]}" update "user_id:0" --user-id 1 --yes 2>&1 | grep -v tui.yaml | sed 's/\x1b\[[0-9;]*m//g'

step "§8 step 7: verify"
podman run --rm --network $NET -v ./move.py:/move.py:ro -v "$DEV_DATA:/out:ro" -v "$DEV_DATA/secrets:/run/dev-secrets:ro" \
    $PY_IMAGE python3 /move.py verify /out/before.json | tee "$DEV_DATA/move-verify.txt"

step "gate 0 in Playwright"
set +e
"$PYTHON" gate0.py
failed=$?
set -e

if [[ -z ${KEEP:-} ]]; then
    step "down"
    compose down -v >/dev/null
fi
echo "gate0: $failed failed check(s)"
exit $failed
