#!/usr/bin/env bash
# Streams the sparsh_los app into the frappe-bench container, installs it on the
# target site, and (unless skipped) runs the behavioural verification harness.
# Idempotent: safe to re-run.
set -euo pipefail

SITE="${SITE:-erp.sssihms.org}"
CONTAINER="${CONTAINER:-internal-backend-1}"
SSH_KEY="${SSH_KEY:-~/Downloads/sssihms-web-vm2023_key.pem}"
SSH_HOST="${SSH_HOST:-azureuser@20.219.253.136}"
SSH_PORT="${SSH_PORT:-2222}"
SKIP_VERIFY="${SKIP_VERIFY:-0}"
MIN_CHECKS="${MIN_CHECKS:-65}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="/home/frappe/frappe-bench"
APP_DIR="${BENCH_DIR}/apps/sparsh_los"

ssh_cmd() {
	# shellcheck disable=SC2029
	ssh -i "${SSH_KEY}" -p "${SSH_PORT}" "${SSH_HOST}" "$@"
}

echo "==> Streaming sparsh_los into ${CONTAINER}:${APP_DIR}"
ssh_cmd "docker exec -u root ${CONTAINER} bash -lc 'rm -rf ${APP_DIR} && mkdir -p ${APP_DIR}'"
tar -C "${REPO_DIR}" \
	--exclude='.git' \
	--exclude='__pycache__' \
	--exclude='*.pyc' \
	-cf - . \
	| ssh_cmd "docker exec -i -u root ${CONTAINER} tar -C ${APP_DIR} -xf -"

echo "==> Fixing ownership"
ssh_cmd "docker exec -u root ${CONTAINER} bash -lc 'chown -R frappe:frappe ${APP_DIR}'"

echo "==> Registering app with bench"
ssh_cmd "docker exec ${CONTAINER} bash -lc '
	cd ${BENCH_DIR} &&
	if bench get-app ${APP_DIR}; then
		echo \"get-app succeeded\";
	else
		echo \"get-app failed, falling back to pip install -e\";
		env/bin/pip install -e apps/sparsh_los;
		grep -qx sparsh_los sites/apps.txt || echo sparsh_los >> sites/apps.txt;
	fi
'"

echo "==> Installing app on site ${SITE}"
ssh_cmd "docker exec ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} install-app sparsh_los'"

echo "==> Clearing cache"
ssh_cmd "docker exec ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} clear-cache'"

echo "==> App list"
ssh_cmd "docker exec ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} list-apps | grep sparsh_los'"

echo "==> DocType count for module 'Sparsh LOS'"
ssh_cmd "docker exec ${CONTAINER} bash -lc \"cd ${BENCH_DIR} && bench --site ${SITE} mariadb --execute=\\\"select count(*) from tabDocType where module='Sparsh LOS' and istable=0\\\"\""

if [ "${SKIP_VERIFY}" != "1" ]; then
	echo "==> Running behavioural verification harness"
	OUTPUT="$(ssh_cmd "docker exec ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} execute sparsh_los.verify.run'")"
	echo "${OUTPUT}"
	if ! echo "${OUTPUT}" | grep -q 'RESULT passed='; then
		echo "FAIL: verify harness did not print a RESULT line" >&2
		exit 1
	fi
	# An empty CHECKS tuple would print "passed=0 failed=0" and look like success.
	PASSED=$(echo "${OUTPUT}" | sed -n 's/.*RESULT passed=\([0-9]*\).*/\1/p')
	if [ "${PASSED:-0}" -lt "${MIN_CHECKS:-1}" ]; then
		echo "FAIL: only ${PASSED:-0} checks ran, expected at least ${MIN_CHECKS:-1}" >&2
		exit 1
	fi

	if echo "${OUTPUT}" | grep -E 'RESULT passed=[0-9]+ failed=[1-9]'; then
		echo "FAIL: verify harness reported failures" >&2
		exit 1
	fi
else
	echo "==> SKIP_VERIFY=1, not running sparsh_los.verify.run"
fi

echo "==> Done"
