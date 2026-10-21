#!/usr/bin/env bash
set -euo pipefail
exec make -C "$(dirname "$0")" "$@"
