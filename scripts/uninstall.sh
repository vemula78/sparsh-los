#!/usr/bin/env bash
# Reverses install_verify.sh. Safe to run when sparsh_los is already absent.
# Same TARGET convention: local by default, remote only when asked for.
set -euo pipefail

TARGET="${TARGET:-local}"

case "${TARGET}" in
local)
	SITE="${SITE:-sparsh.localhost}"
	CONTAINER="${CONTAINER:-frappe_docker-backend-1}"
	;;
remote)
	SITE="${SITE:-}"
	CONTAINER="${CONTAINER:-internal-backend-1}"
	# Deliberately no defaults. The address, user and key of a hospital server do not
	# belong in version control -- a private repository is still a copy of them, and a
	# repository changes hands more easily than a server does. Supply them per-invocation:
	#   SSH_HOST=user@host SSH_KEY=~/path/key.pem TARGET=remote ./scripts/install_verify.sh
	SSH_KEY="${SSH_KEY:-}"
	SSH_HOST="${SSH_HOST:-}"
	SSH_PORT="${SSH_PORT:-2222}"
	if [ -z "${SSH_HOST}" ] || [ -z "${SSH_KEY}" ] || [ -z "${SITE}" ]; then
		echo "TARGET=remote needs SSH_HOST, SSH_KEY and SITE in the environment." >&2
		echo "  SSH_HOST=user@host SSH_KEY=~/path/key.pem SITE=<site> TARGET=remote $0" >&2
		exit 2
	fi
	;;
*)
	echo "TARGET must be 'local' or 'remote', got '${TARGET}'" >&2
	exit 2
	;;
esac

BENCH_DIR="/home/frappe/frappe-bench"
APP_DIR="${BENCH_DIR}/apps/sparsh_los"

host_cmd() {
	if [ "${TARGET}" = "local" ]; then
		bash -c "$1"
	else
		# shellcheck disable=SC2029
		ssh -i "${SSH_KEY}" -p "${SSH_PORT}" "${SSH_HOST}" "$1"
	fi
}

echo "==> Target ${TARGET}: ${CONTAINER} site ${SITE}"

echo "==> Uninstalling sparsh_los from site ${SITE} (if installed)"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} uninstall-app sparsh_los --yes --no-backup --force || true'"

echo "==> Removing from apps.txt"
host_cmd "docker exec -u root ${CONTAINER} bash -lc 'sed -i \"/^sparsh_los\$/d\" ${BENCH_DIR}/sites/apps.txt || true'"

echo "==> Removing app directory"
host_cmd "docker exec -u root ${CONTAINER} bash -lc 'rm -rf ${APP_DIR}'"

echo "==> DocType count for module 'Sparsh LOS' (expected 0)"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc \"cd ${BENCH_DIR} && bench --site ${SITE} mariadb --execute=\\\"select count(*) from tabDocType where module='Sparsh LOS' and istable=0\\\"\""

echo "==> Done"
