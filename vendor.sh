#!/usr/bin/env bash
# vendor.sh <dest dir> [ref] - copy the vaultkit package (and ui/: machiya.css, machiya.js) at <ref> (default: HEAD;
# use a tag) into <dest>/vaultkit/ (ui/ into <dest>/vaultkit/ui/),
# replacing what's there, and write <dest>/vaultkit/VENDORED: the version line, then a sha256 per file.
# `python3 -m vaultkit.verify` (run by each service's tests) fails if the copy is later edited in place.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dest="${1:?usage: vendor.sh <dest dir> [ref]}"
ref="${2:-HEAD}"
[[ -d $dest ]] || { echo "vendor.sh: $dest is not a directory" >&2; exit 1; }

desc=$(git -C "$here" describe --tags --match 'v[0-9]*' --always "$ref")   # vaultkit's own tags (not mcp-v…, landing-v…)
[[ $ref == HEAD && -n $(git -C "$here" status --porcelain -- vaultkit) ]] && desc="$desc-dirty"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -C "$here" archive "$ref" vaultkit $(git -C "$here" ls-tree -d --name-only "$ref" ui) | tar -x -C "$tmp"
if [[ $desc == *-dirty ]]; then                                      # testing uncommitted changes
    cp "$here"/vaultkit/*.py "$tmp/vaultkit/"
    [[ -d $here/ui ]] && mkdir -p "$tmp/ui" && cp "$here"/ui/* "$tmp/ui/"
fi

rm -rf "$dest/vaultkit"
mkdir "$dest/vaultkit"
cp "$tmp"/vaultkit/*.py "$dest/vaultkit/"
if [[ -d $tmp/ui ]]; then                                            # the shared stylesheet/script (vaultkit >= 0.4)
    mkdir "$dest/vaultkit/ui"
    cp "$tmp"/ui/* "$dest/vaultkit/ui/"
fi
{
    echo "# vaultkit $desc - vendored by vendor.sh; don't edit these files"
    (cd "$dest/vaultkit" && sha256sum -- *.py && { [[ -d ui ]] && sha256sum -- ui/* || true; })
} >"$dest/vaultkit/VENDORED"
echo "vendor.sh: vaultkit $desc -> $dest/vaultkit"
