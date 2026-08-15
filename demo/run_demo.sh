#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

printf '%s\n' '== Scan: show the resolved upload surface =='
artifact-fence scan "$ROOT/demo/fixture"
printf '\n%s\n' '== Check: fail CI on the high-severity upload exposure =='
if artifact-fence check "$ROOT/demo/fixture"; then
  echo 'Unexpected: the demo should fail the high-severity gate.' >&2
  exit 1
else
  echo 'Expected gate failure: build/** includes build/.env.'
fi
