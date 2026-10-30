#!/bin/bash
# Único punto de entrada aprobado para Pi en Cloud Agents (Fase 1).
# Fase 1: solo --check. No envía prompts, no autentica y no llama a un modelo.
#
# Revisiones futuras (aún no habilitadas) usarán únicamente:
#   pi --no-session --tools read,grep,find,ls
# Nunca se exponen bash, write ni edit.
set -euo pipefail

# Controlled PATH before any external command. Do not append caller PATH.
PATH="/usr/local/cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
# Node loader hooks must never reach the pinned CLI: an ambient NODE_OPTIONS
# (--require/--import) or NODE_PATH would load attacker JavaScript into the
# trusted Node process before the authenticated CLI runs.
unset NODE_OPTIONS NODE_PATH NODE_V8_COVERAGE
hash -r

PI_PACKAGE="@earendil-works/pi-coding-agent"
PI_EXPECTED_VERSION="0.84.4"
PI_READONLY_TOOLS="read,grep,find,ls"
PI_FORBIDDEN_TOOLS="bash,write,edit"
# Builtin-only root: parameter expansion + cd + pwd -P. No external utility.
_pi_review_src="${BASH_SOURCE[0]}"
case "$_pi_review_src" in
  */*) ;;
  *) _pi_review_src="./${_pi_review_src}" ;;
esac
PI_REVIEW_ROOT="$(cd "${_pi_review_src%/*}/.." && pwd -P)"
unset _pi_review_src
# Hardcoded trust root. Must stay equal to install.sh NODE_VERSION.
# Do not override from the environment.
PI_PINNED_NODE_PREFIX="/usr/local/lib/nodejs/node-v22.23.2"
PI_PINNED_NODE="${PI_PINNED_NODE_PREFIX}/bin/node"
PI_PINNED_BIN="${PI_PINNED_NODE_PREFIX}/bin/pi"
PI_PINNED_CLI="${PI_PINNED_NODE_PREFIX}/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"

pi_review_fail() {
  echo "!! $*" >&2
  exit 1
}

pi_review_path_is_beneath() {
  local child="$1"
  local parent="$2"
  child="${child%/}"
  parent="${parent%/}"
  [ -n "$child" ] && [ -n "$parent" ] || return 1
  case "$child" in
    "${parent}"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Fail-closed prefix metadata scan. A traversal error (nonzero find status)
# rejects the prefix even when the partial stdout is empty.
pi_review_scan_clean() {
  local prefix="$1"
  shift
  local hits rc
  # Absolute scanner: a PATH-resolved find could be attacker-controlled and
  # report a hostile prefix as clean.
  [ -x /usr/bin/find ] || return 1
  rc=0
  hits="$(/usr/bin/find "$prefix" "$@" -print)" || rc=$?
  [ "$rc" -eq 0 ] && [ -z "$hits" ]
}

# Standalone equivalent of install.sh cloud_tools_directory_is_root_protected.
# Do not source install.sh from this wrapper.
pi_review_directory_is_root_protected() {
  local dir="$1"
  local canonical uid gid mode writable
  [ -n "$dir" ] || return 1
  [ -d "$dir" ] || return 1
  [ ! -L "$dir" ] || return 1
  [ -x /usr/bin/stat ] || return 1
  [ -x /usr/bin/readlink ] || return 1
  canonical="$(/usr/bin/readlink -f -- "$dir")" || return 1
  [ "${canonical%/}" = "${dir%/}" ] || return 1
  uid="$(/usr/bin/stat -c '%u' -- "$dir")" || return 1
  gid="$(/usr/bin/stat -c '%g' -- "$dir")" || return 1
  mode="$(/usr/bin/stat -c '%a' -- "$dir")" || return 1
  [ "$uid" = "0" ] && [ "$gid" = "0" ] || return 1
  [ -n "$mode" ] || return 1
  writable=$((8#${mode} & 022))
  [ "$writable" -eq 0 ]
}

# Standalone equivalent of install.sh cloud_tools_parent_chain_is_root_protected.
pi_review_parent_chain_is_root_protected() {
  local path="$1"
  local current
  current="${path%/}"
  [ -n "$current" ] || return 1
  if [ "$current" = "/" ]; then
    pi_review_directory_is_root_protected "/"
    return $?
  fi
  while [ "$current" != "/" ]; do
    current="${current%/*}"
    if [ -z "$current" ]; then
      current="/"
    fi
    pi_review_directory_is_root_protected "$current" || return 1
  done
  return 0
}

# Authenticate the immutable trust chain before any Node/Pi execution.
pi_review_assert_trust_chain() {
  local pi_path="$1"
  local literal_prefix canonical_prefix canonical_node canonical_cli canonical_bin resolved
  if [ -z "$pi_path" ]; then
    pi_review_fail "pi no está en PATH"
  fi
  if [ "$pi_path" = "/exec-daemon/pi" ]; then
    pi_review_fail "pi resolvió a /exec-daemon/pi; use el binario fijado del Cloud Build"
  fi
  if [ ! -x "$pi_path" ]; then
    pi_review_fail "pi en ${pi_path} no es ejecutable"
  fi
  if [ ! -x /usr/bin/readlink ]; then
    pi_review_fail "readlink no está disponible para resolver pi"
  fi
  literal_prefix="${PI_PINNED_NODE_PREFIX%/}"
  if [ ! -d "$PI_PINNED_NODE_PREFIX" ]; then
    pi_review_fail "falta el prefijo Node fijado (${PI_PINNED_NODE_PREFIX})"
  fi
  canonical_prefix="$(/usr/bin/readlink -f "$PI_PINNED_NODE_PREFIX")" || canonical_prefix=""
  canonical_prefix="${canonical_prefix%/}"
  if [ -z "$canonical_prefix" ] || [ "$canonical_prefix" != "$literal_prefix" ]; then
    pi_review_fail "el prefijo Node fijado no es el directorio literal (${PI_PINNED_NODE_PREFIX} -> ${canonical_prefix:-?})"
  fi
  if ! pi_review_parent_chain_is_root_protected "$PI_PINNED_NODE_PREFIX"; then
    pi_review_fail "la cadena de directorios del prefijo Node no es root ni está endurecida (${PI_PINNED_NODE_PREFIX})"
  fi
  # Provenance before execution: a user-owned or writable entry lets a
  # non-root agent replace bin/node or the Pi CLI in place and have it
  # report the expected version. Reject before any Node invocation.
  if ! pi_review_scan_clean "$PI_PINNED_NODE_PREFIX" \( ! -user root -o ! -group root \) \
    || ! pi_review_scan_clean "$PI_PINNED_NODE_PREFIX" ! -type l -perm /022; then
    pi_review_fail "el prefijo Node fijado no es root ni está endurecido (${PI_PINNED_NODE_PREFIX})"
  fi
  if [ ! -x "$PI_PINNED_NODE" ]; then
    pi_review_fail "falta Node en el prefijo fijado (${PI_PINNED_NODE})"
  fi
  canonical_node="$(/usr/bin/readlink -f "$PI_PINNED_NODE")" || canonical_node=""
  canonical_node="${canonical_node%/}"
  if [ "$canonical_node" != "${literal_prefix}/bin/node" ]; then
    pi_review_fail "Node (${PI_PINNED_NODE} -> ${canonical_node:-?}) no es el ejecutable fijado"
  fi
  case "$canonical_node" in
    /exec-daemon/*)
      pi_review_fail "Node resuelve a ${canonical_node}"
      ;;
  esac
  if [ ! -f "$PI_PINNED_CLI" ]; then
    pi_review_fail "falta el CLI fijado de Pi (${PI_PINNED_CLI})"
  fi
  canonical_cli="$(/usr/bin/readlink -f "$PI_PINNED_CLI")" || canonical_cli=""
  canonical_cli="${canonical_cli%/}"
  if [ "$canonical_cli" != "$PI_PINNED_CLI" ]; then
    pi_review_fail "el CLI de Pi (${PI_PINNED_CLI} -> ${canonical_cli:-?}) no es el path fijado"
  fi
  if [ ! -e "$PI_PINNED_BIN" ]; then
    pi_review_fail "falta pi en el prefijo Node fijado (${PI_PINNED_BIN})"
  fi
  canonical_bin="$(/usr/bin/readlink -f "$PI_PINNED_BIN")" || canonical_bin=""
  canonical_bin="${canonical_bin%/}"
  if [ "$canonical_bin" != "$canonical_cli" ]; then
    pi_review_fail "pi (${PI_PINNED_BIN} -> ${canonical_bin:-?}) no es el CLI fijado (${canonical_cli})"
  fi
  resolved="$(/usr/bin/readlink -f "$pi_path")" || resolved=""
  resolved="${resolved%/}"
  case "$resolved" in
    /exec-daemon/*)
      pi_review_fail "pi resuelve a ${resolved}"
      ;;
  esac
  if [ "$resolved" != "$canonical_cli" ]; then
    pi_review_fail "pi en PATH (${pi_path} -> ${resolved}) no es el CLI fijado (${canonical_cli})"
  fi
  if ! pi_review_path_is_beneath "$resolved" "$literal_prefix"; then
    pi_review_fail "pi (${pi_path} -> ${resolved}) no está bajo el prefijo fijado (${literal_prefix})"
  fi
}

pi_review_mktemp_home() {
  local dir canonical_dir canonical_root
  if [ ! -d /tmp ] || [ ! -w /tmp ]; then
    return 1
  fi
  dir="$(/usr/bin/mktemp -d --tmpdir=/tmp pi-probe.XXXXXX)" || return 1
  canonical_dir="$(/usr/bin/readlink -f "$dir")" || canonical_dir=""
  canonical_root="$(/usr/bin/readlink -f "$PI_REVIEW_ROOT")" || canonical_root=""
  if [ -z "$canonical_dir" ] || [ -z "$canonical_root" ]; then
    rm -rf "$dir"
    return 1
  fi
  canonical_dir="${canonical_dir%/}"
  canonical_root="${canonical_root%/}"
  if [ "$canonical_dir" = "$canonical_root" ] || pi_review_path_is_beneath "$canonical_dir" "$canonical_root"; then
    rm -rf "$dir"
    return 1
  fi
  printf '%s' "$canonical_dir"
}

pi_review_version_token() {
  local output="$1"
  local token
  # shellcheck disable=SC2086
  for token in $output; do
    case "$token" in
      v[0-9]*.[0-9]*)
        printf '%s' "${token#v}"
        return 0
        ;;
      [0-9]*.[0-9]*)
        printf '%s' "$token"
        return 0
        ;;
    esac
  done
  return 1
}

usage() {
  cat <<'EOF'
Uso:
  .cursor/pi-review.sh --check

Fase 1 solo valida la instalación read-only. No envía prompts ni requiere
autenticación. El modo de revisión con modelo no está habilitado.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ "$#" -ne 1 ] || [ "$1" != "--check" ]; then
  usage >&2
  echo "!! Fase 1: solo está aprobado --check. No se invocará Pi con un prompt." >&2
  exit 2
fi

hash -r
pi_path="$(command -v pi || true)"
pi_review_assert_trust_chain "$pi_path"

# Aislar HOME: `pi --version` escribiría ~/.pi/agent/auth.json en el home real.
check_home=""
if ! check_home="$(pi_review_mktemp_home)"; then
  pi_review_fail "no se pudo crear un HOME temporal seguro para Pi"
fi
if [ -z "$check_home" ]; then
  pi_review_fail "no se pudo crear un HOME temporal seguro para Pi"
fi
# shellcheck disable=SC2064
trap 'rm -rf "$check_home"' EXIT
if [ ! -x /usr/bin/env ]; then
  pi_review_fail "env no está disponible para aislar la sonda de Pi"
fi
if ! pi_output="$(/usr/bin/env -i HOME="$check_home" PATH="$PATH" "$PI_PINNED_NODE" "$PI_PINNED_CLI" --version 2>&1)"; then
  pi_review_fail "pi --version falló (${pi_output})"
fi
pi_reported="$(pi_review_version_token "$pi_output")" || pi_review_fail "pi --version no reportó una versión (${pi_output})"
if [ "$pi_reported" != "$PI_EXPECTED_VERSION" ]; then
  pi_review_fail "pi reportó ${pi_reported}; se esperaba ${PI_EXPECTED_VERSION}"
fi
if [ -e "${check_home}/.pi/agent/auth.json" ]; then
  echo "  note: isolated probe wrote a throwaway auth.json under mktemp (not ~/.pi)"
fi

case ",${PI_READONLY_TOOLS}," in
  *,bash,*|*,write,*|*,edit,*)
    pi_review_fail "la allowlist read-only incluye una herramienta prohibida: ${PI_READONLY_TOOLS}"
    ;;
esac
for forbidden in bash write edit; do
  case ",${PI_FORBIDDEN_TOOLS}," in
    *,${forbidden},*) ;;
    *) pi_review_fail "PI_FORBIDDEN_TOOLS no declara ${forbidden}" ;;
  esac
  case ",${PI_READONLY_TOOLS}," in
    *,${forbidden},*)
      pi_review_fail "${forbidden} no puede formar parte de PI_READONLY_TOOLS"
      ;;
  esac
done

echo "pi-review check: ok"
echo "  package:     ${PI_PACKAGE}@${PI_EXPECTED_VERSION}"
echo "  binary:      ${pi_path}"
echo "  version:     ${pi_reported}"
echo "  tools:       ${PI_READONLY_TOOLS}"
echo "  forbidden:   ${PI_FORBIDDEN_TOOLS}"
echo "  session:     --no-session (futuro; no se invocó Pi con prompt)"
echo "  auth:        none required for --check"
echo "  prompt:      not sent"
echo "  provider:    not selected in Phase 1"
echo "  model:       not selected in Phase 1"
echo "  llm_call:    no"
