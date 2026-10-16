#!/usr/bin/env bash
# publish.sh — put the autonomouse wheel, and the install.sh that pins it, where a web server
# serves them, together. Use it to host your own install command.
#
#   setup/publish.sh --url URL                          dry run: build, digest, print what --apply would do
#   setup/publish.sh --url URL --apply --host user@host --remote-dir DIR
#                                                       copy both files to DIR on that host over ssh
#   setup/publish.sh --apply --dest DIR                 copy both files into the local folder DIR
#
#   --url URL         the URL the folder is served at: the wheel URL pinned in install.sh, and
#                     what the check reads back (with --dest it defaults to file://DIR)
#   --host user@host  the ssh login of the host that serves the folder (env PUBLISH_HOST)
#   --remote-dir DIR  the folder on that host that URL points at (env PUBLISH_REMOTE_DIR)
#   --key FILE        the ssh key to log in with (env PUBLISH_SSH_KEY; default: ssh's own)
#   --url also reads the env PUBLISH_URL.
#
# install.sh verifies WHEEL_SHA256 before it installs the wheel at WHEEL_URL, so the two files
# are a matched pair: a wheel placed without its pin makes every install die on the checksum.
# The pair is a function of HEAD alone. The wheel is built from `git archive HEAD`, and the
# install.sh published is HEAD's setup/install.sh with the two lines filled in. The pin lives
# only in the published copy; the repository's setup/install.sh keeps them empty.
#
# It refuses a dirty tree, a HEAD no branch on origin contains, and pyproject.toml's version
# when that is the version the destination already serves (its wheel name, read from the
# served install.sh) — a release (autonomouse release major|minor|patch) bumps the version
# first. It ends by reading back what the destination serves and deciding PASS or FAIL on
# that alone: the digest inside the served install.sh equals the SHA-256 of the served
# wheel, and both equal what this run built.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required and is not on PATH"; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

APPLY=no DEST="" BASE_URL="${PUBLISH_URL:-}"
TARGET="${PUBLISH_HOST:-}" REMOTE_DIR="${PUBLISH_REMOTE_DIR:-}" SSH_KEY="${PUBLISH_SSH_KEY:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes; shift ;;
    --dest)  DEST="${2:?--dest needs a folder}"; shift 2 ;;
    --url)   BASE_URL="${2:?--url needs a URL}"; shift 2 ;;
    --host)  TARGET="${2:?--host needs user@host}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:?--remote-dir needs a folder}"; shift 2 ;;
    --key)   SSH_KEY="${2:?--key needs a file}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "publish.sh: unknown argument: $1" >&2; usage ;;
  esac
done
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[ -z "$SSH_KEY" ] || SSH_OPTS+=(-i "$SSH_KEY")
if [ -n "$DEST" ]; then
  DEST="$(realpath -m "$DEST")"
  BASE_URL="${BASE_URL:-file://$DEST}"
  WHERE="the local folder $DEST"
else
  [ -n "$BASE_URL" ] || die "no URL to pin in install.sh. Pass --url URL (or set PUBLISH_URL), or --dest DIR."
  if [ "$APPLY" = yes ]; then
    [ -n "$TARGET" ] && [ -n "$REMOTE_DIR" ] \
      || die "--apply without --dest needs --host user@host and --remote-dir DIR (or PUBLISH_HOST and PUBLISH_REMOTE_DIR)."
  fi
  WHERE="${REMOTE_DIR:-the folder at --remote-dir} on ${TARGET:-the host at --host}"
fi
BASE_URL="${BASE_URL%/}"

# --- step 0: preconditions -------------------------------------------------------------
step "step 0: preconditions"
need git; need uv; need curl; need sha256sum; need tar
if [ -z "$DEST" ] && [ "$APPLY" = yes ]; then
  need ssh; need scp
fi
say "publishing from $REPO to $WHERE"
say "served at $BASE_URL"
[ "$APPLY" = yes ] || say "DRY RUN: nothing is copied and install.sh is not changed; --apply publishes."

# --- step 1: the tree is clean ---------------------------------------------------------
step "step 1: working tree"
DIRTY="$(git -C "$REPO" status --porcelain)"
[ -z "$DIRTY" ] || die "the working tree is dirty. Commit or stash these, then re-run:
$DIRTY"
HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
say "clean at $HEAD_SHA"

# --- step 2: HEAD is on origin ---------------------------------------------------------
step "step 2: HEAD is pushed"
git -C "$REPO" fetch --quiet origin || die "cannot reach origin, so whether HEAD is pushed is unknown. Not publishing."
CONTAINS="$(git -C "$REPO" for-each-ref --contains HEAD --format='%(refname:short)' refs/remotes | grep -v '/HEAD$' || true)"
[ -n "$CONTAINS" ] || die "HEAD $HEAD_SHA is on no branch at origin. Push it, then re-run."
say "reachable from: $(echo $CONTAINS)"

BUILD="$(mktemp -d /tmp/autonomouse-publish.XXXXXX)"
trap 'rm -rf "$BUILD"' EXIT

# --- step 3: refuse a version already served --------------------------------------------
# install.sh carries no version line of its own; the served wheel's file name does. A
# version served twice would mean two commits answering to one wheel name, and an install
# caught mid-swap failing its checksum — so this is checked, and refused, before anything
# is built. Only a run with a real destination in mind checks: --apply (the one case this
# protects) or --dest (so a dry run against a chosen destination previews the same
# refusal); a plain preview with neither names nothing to compare against and stays local.
if [ "$APPLY" = yes ] || [ -n "$DEST" ]; then
  step "step 3: what $BASE_URL is serving"
  VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO/pyproject.toml")"
  [ "$(printf '%s\n' "$VERSION" | grep -c .)" = 1 ] \
    || die "pyproject.toml must have exactly one line \`version = \"X.Y.Z\"\` — found $(printf '%s\n' "$VERSION" | grep -c .)"
  say "version to publish: $VERSION"
  SERVED_INSTALL="$BUILD/gate-served-install.sh"
  SERVED_VERSION=""
  if curl -fsSL -H 'Cache-Control: no-cache' -o "$SERVED_INSTALL" "$BASE_URL/install.sh" 2>/dev/null; then
    SERVED_WHEEL_URL="$(sed -n 's/^WHEEL_URL="\(.*\)"$/\1/p' "$SERVED_INSTALL")"
    SERVED_VERSION="$(printf '%s\n' "$SERVED_WHEEL_URL" | sed -n 's|.*/autonomouse-\(.*\)-py3-none-any\.whl$|\1|p')"
  else
    CURL_STATUS=$?
    # 22 (HTTP error, curl -f) and 37 (file:// not found) both mean "nothing served there
    # yet" — safe to proceed. Any other code (host unreachable, timeout, ...) means the
    # answer is unknown, and an unknown answer must not wave a served version through.
    case "$CURL_STATUS" in
      22 | 37) ;;
      *) die "cannot tell what $BASE_URL/install.sh serves (curl exit $CURL_STATUS). Not publishing until that is known." ;;
    esac
  fi
  if [ -n "$SERVED_VERSION" ]; then
    say "version served: $SERVED_VERSION"
    if [ "$SERVED_VERSION" = "$VERSION" ]; then
      die "$BASE_URL already serves autonomouse $VERSION. Publishing the same version again could
put a different commit's wheel under a name an install already trusts. Cut a release first
(autonomouse release major|minor|patch), then publish."
    fi
  else
    say "version served: none ($BASE_URL/install.sh not served yet, or names no wheel)"
  fi
fi

# --- step 4: build the wheel from a clean checkout of HEAD ------------------------------
# `git archive HEAD`, not the working tree: the tree carries .venv, dist/ and caches, and the
# wheel must be a function of the commit alone.
step "step 4: build the wheel from a clean checkout of $HEAD_SHA"
mkdir "$BUILD/src"
git -C "$REPO" archive HEAD | tar -x -C "$BUILD/src"
uv build --wheel -q -o "$BUILD/dist" "$BUILD/src"
WHEELS=("$BUILD"/dist/*.whl)
[ "${#WHEELS[@]}" -eq 1 ] || die "the build produced ${#WHEELS[@]} wheels, so which one to publish is ambiguous"
WHEEL="${WHEELS[0]}"
WHEEL_NAME="$(basename "$WHEEL")"
DIGEST="$(sha256_of "$WHEEL")"
say "built  $WHEEL_NAME"
say "sha256 $DIGEST"

# --- step 5: pin the wheel in HEAD's install.sh ----------------------------------------
step "step 5: pin the wheel in install.sh"
PINNED="$BUILD/install.sh"
SOURCE="$BUILD/src/setup/install.sh"
# Exactly one line of each: a reformatted install.sh must stop the publish, never publish a
# copy whose pin the sed below silently missed.
[ "$(grep -c '^WHEEL_URL=' "$SOURCE")" = 1 ] && [ "$(grep -c '^WHEEL_SHA256=' "$SOURCE")" = 1 ] \
  || die "setup/install.sh at HEAD does not have exactly one WHEEL_URL= and one WHEEL_SHA256= line"
sed -e "s|^WHEEL_URL=.*\$|WHEEL_URL=\"$BASE_URL/$WHEEL_NAME\"|" \
    -e "s|^WHEEL_SHA256=.*\$|WHEEL_SHA256=\"$DIGEST\"|" "$SOURCE" >"$PINNED"
bash -n "$PINNED" || die "the pinned install.sh does not parse"
grep -E '^WHEEL_(URL|SHA256)=' "$PINNED" | sed 's/^/  /'

if [ "$APPLY" = no ]; then
  step "dry run: what --apply would do"
  say "copy $WHEEL_NAME, then install.sh with the pin above, to $WHERE"
  say "read both back from $BASE_URL and check that the served install.sh pins the served wheel"
  say "setup/install.sh in $REPO is not changed either way"
  exit 0
fi

# --- step 6: copy both, staged then swapped --------------------------------------------
# Copying straight over install.sh would leave a truncated file reachable under the name
# people pipe into bash. Both land under dot-names and move into place in one shell, the
# wheel first: its name carries the version, so until install.sh moves the old pair still
# agrees.
step "step 6: copy both to $WHERE"
if [ -n "$DEST" ]; then
  mkdir -p "$DEST"
  cp "$WHEEL" "$DEST/.publish-wheel.new"
  cp "$PINNED" "$DEST/.publish-install.new"
  chmod 644 "$DEST/.publish-wheel.new" "$DEST/.publish-install.new"
  mv "$DEST/.publish-wheel.new" "$DEST/$WHEEL_NAME"
  mv "$DEST/.publish-install.new" "$DEST/install.sh"
else
  ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p '$REMOTE_DIR' && chmod 755 '$REMOTE_DIR'"
  scp "${SSH_OPTS[@]}" -q "$WHEEL" "$TARGET:$REMOTE_DIR/.publish-wheel.new"
  scp "${SSH_OPTS[@]}" -q "$PINNED" "$TARGET:$REMOTE_DIR/.publish-install.new"
  ssh "${SSH_OPTS[@]}" "$TARGET" "set -eu
cd '$REMOTE_DIR'
chmod 644 .publish-wheel.new .publish-install.new
mv .publish-wheel.new '$WHEEL_NAME'
mv .publish-install.new install.sh"
fi
say "placed $WHEEL_NAME, then install.sh"

# --- step 7: read back what is served --------------------------------------------------
# The deciding check reads the destination back rather than trusting anything local.
step "step 7: verify what $BASE_URL serves"
curl -fsSL -H 'Cache-Control: no-cache' -o "$BUILD/served-install.sh" "$BASE_URL/install.sh" \
  || die "cannot fetch $BASE_URL/install.sh"
curl -fsSL -H 'Cache-Control: no-cache' -o "$BUILD/served.whl" "$BASE_URL/$WHEEL_NAME" \
  || die "cannot fetch $BASE_URL/$WHEEL_NAME"
SERVED_WHEEL_SHA="$(sha256_of "$BUILD/served.whl")"
SERVED_PIN="$(sed -n 's/^WHEEL_SHA256="\(.*\)"$/\1/p' "$BUILD/served-install.sh")"
SERVED_URL="$(sed -n 's/^WHEEL_URL="\(.*\)"$/\1/p' "$BUILD/served-install.sh")"

FAILED=0
verdict() {  # label, got, want
  if [ "$2" = "$3" ]; then printf '  OK    %s\n' "$1"
  else printf '  FAIL  %s\n          %s\n          %s\n' "$1" "$2" "$3"; FAILED=$((FAILED + 1)); fi
}
verdict "the served install.sh pins the served wheel"  "$SERVED_PIN"        "$SERVED_WHEEL_SHA"
verdict "the served install.sh names the served wheel" "$SERVED_URL"        "$BASE_URL/$WHEEL_NAME"
verdict "the served wheel is the wheel built here"     "$SERVED_WHEEL_SHA"  "$DIGEST"
verdict "the served install.sh is the one pinned here" "$(sha256_of "$BUILD/served-install.sh")" "$(sha256_of "$PINNED")"
if [ "$FAILED" -ne 0 ]; then
  printf '\nPUBLISH: FAIL - %d check(s) above\n' "$FAILED"
  die "$BASE_URL serves a pair that does not agree; every install dies on the checksum until it does."
fi
printf '\nPUBLISH: PASS\n'

step "done"
say "curl -fsSL $BASE_URL/install.sh | bash   installs $WHEEL_NAME ($HEAD_SHA)"
