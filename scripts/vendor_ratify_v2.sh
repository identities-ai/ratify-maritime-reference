#!/usr/bin/env bash
# Export the Ratify Protocol v2 Python SDK sources this reference builds
# against into vendor/ratify-protocol/.
#
# RATIFY_V2_PIN: Protocol v2 is not yet on PyPI. Until ratify-protocol-v2 and
# ratify-protocol-native-v2 2.0.0b1 are published, this reference installs
# both from a ratify-protocol checkout. When they are published, replace the
# [tool.uv.sources] entries in pyproject.toml with exact ==2.0.0b1 pins,
# re-lock, delete this script and vendor/, and drop the vendor COPY lines from
# both Dockerfiles.
#
# Usage: scripts/vendor_ratify_v2.sh /path/to/ratify-protocol
#
# The checkout must be clean. Only tracked files at its HEAD are exported, and
# the commit is recorded in vendor/ratify-protocol/REVISION so every build
# names exactly which protocol source it used. The native extension builds
# from source with maturin, so a Rust toolchain is required.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/ratify-protocol" >&2
  exit 2
fi
PROTOCOL="$(cd "$1" && pwd)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/ratify-protocol"

if [[ -n "$(git -C "$PROTOCOL" status --porcelain)" ]]; then
  echo "refusing to vendor from a checkout with uncommitted changes: $PROTOCOL" >&2
  exit 1
fi
REVISION="$(git -C "$PROTOCOL" rev-parse HEAD)"

rm -rf "$DEST"
mkdir -p "$DEST"
git -C "$PROTOCOL" archive --format=tar HEAD \
  LICENSE sdks/rust sdks/python-v2 sdks/python-native-v2 \
  | tar -x -C "$DEST"
# The SDK test suites are not needed to build or run this reference.
rm -rf "$DEST/sdks/python-v2/tests" "$DEST/sdks/rust/fuzz"
echo "$REVISION" > "$DEST/REVISION"
echo "vendored ratify-protocol $REVISION into vendor/ratify-protocol"
