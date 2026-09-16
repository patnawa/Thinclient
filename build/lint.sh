#!/bin/bash
# Gate correctness errors across all tracked shell entry points.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v shellcheck >/dev/null || { echo 'install shellcheck' >&2; exit 2; }
failed=0
scratch="$(mktemp)"
trap 'rm -f -- "$scratch"' EXIT
while IFS= read -r -d '' file; do
    case "$(head -1 "$file")" in
        '#!/bin/sh'*|'#!/bin/bash'*)
            sed 's/\r$//' "$file" > "$scratch"
            shellcheck --severity=error "$scratch" || { echo "Failed: $file"; failed=1; } ;;
    esac
done < <(find build pxe overlay deploy -type f ! -name '*.local.sh' \
          \( -name '*.sh' -o -name 'tc-*' -o -path '*/dispatcher.d/*' \) -print0)
exit "$failed"
