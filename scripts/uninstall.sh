#!/usr/bin/env bash
# Reverses install_verify.sh. Safe to run when sparsh_los is already absent.
set -euo pipefail

SITE="${SITE:-erp.sssihms.org}"
CONTAINER="${CONTAINER:-internal-backend-1}"
SSH_KEY="${SSH_KEY:-~/Downloads/sssihms-web-vm2023_key.pem}"
SSH_HOST="${SSH_HOST:-azureuser@20.219.253.136}"
SSH_PORT="${SSH_PORT:-2222}"

BENCH_DIR="/home/frappe/frappe-bench"
APP_DIR="${BENCH_DIR}/apps/sparsh_los"

ssh_cmd() {
	# shellcheck disable=SC2029
	ssh -i "${SSH_KEY}" -p "${SSH_PORT}" "${SSH_HOST}" "$@"
}

echo "==> Uninstalling sparsh_los from site ${SITE} (if installed)"
ssh_cmd "docker exec ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} uninstall-app sparsh_los --yes --no-backup --force || true'"

echo "==> Removing from apps.txt"
ssh_cmd "docker exec -u root ${CONTAINER} bash -lc 'sed -i \"/^sparsh_los\$/d\" ${BENCH_DIR}/sites/apps.txt || true'"

echo "==> Removing app directory"
ssh_cmd "docker exec -u root ${CONTAINER} bash -lc 'rm -rf ${APP_DIR}'"

echo "==> DocType count for module 'Sparsh LOS' (expected 0)"
ssh_cmd "docker exec ${CONTAINER} bash -lc \"cd ${BENCH_DIR} && bench --site ${SITE} mariadb --execute=\\\"select count(*) from tabDocType where module='Sparsh LOS' and istable=0\\\"\""

echo "==> Done"
