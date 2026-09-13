#!/usr/bin/env bash
# Streams the sparsh_los app into a frappe-bench container, installs it on the
# target site, and (unless skipped) runs the behavioural verification harness.
# Idempotent: safe to re-run.
#
# Defaults to the LOCAL development bench (frappe_docker on this machine).
# The live hospital bench is not a development environment: reaching it needs
# TARGET=remote set deliberately, so it can never be the accidental default.
set -euo pipefail

TARGET="${TARGET:-local}"
SKIP_VERIFY="${SKIP_VERIFY:-0}"
MIN_CHECKS="${MIN_CHECKS:-111}"

case "${TARGET}" in
local)
	SITE="${SITE:-sparsh.localhost}"
	CONTAINER="${CONTAINER:-frappe_docker-backend-1}"
	;;
remote)
	SITE="${SITE:-erp.sssihms.org}"
	CONTAINER="${CONTAINER:-internal-backend-1}"
	SSH_KEY="${SSH_KEY:-~/Downloads/sssihms-web-vm2023_key.pem}"
	SSH_HOST="${SSH_HOST:-azureuser@20.219.253.136}"
	SSH_PORT="${SSH_PORT:-2222}"
	;;
*)
	echo "TARGET must be 'local' or 'remote', got '${TARGET}'" >&2
	exit 2
	;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="/home/frappe/frappe-bench"
APP_DIR="${BENCH_DIR}/apps/sparsh_los"

# One indirection for both targets: locally `docker exec` runs here, remotely the
# same command is wrapped in ssh. Everything below is written once.
host_cmd() {
	if [ "${TARGET}" = "local" ]; then
		bash -c "$1"
	else
		# shellcheck disable=SC2029
		ssh -i "${SSH_KEY}" -p "${SSH_PORT}" "${SSH_HOST}" "$1"
	fi
}

echo "==> Target ${TARGET}: ${CONTAINER} site ${SITE}"

echo "==> Streaming sparsh_los into ${CONTAINER}:${APP_DIR}"
host_cmd "docker exec -u root ${CONTAINER} bash -lc 'rm -rf ${APP_DIR} && mkdir -p ${APP_DIR}'"
# COPYFILE_DISABLE stops macOS bsdtar writing an AppleDouble `._name` sidecar beside
# every file; the --exclude catches any that are already on disk. Without both, ~150
# of them landed in the container -- 163-byte binaries that are not UTF-8 and sit next
# to every source file. They are not merely untidy: the determinism scanner reads with
# errors="ignore", so it counted them as scanned files and its "did I scan enough?"
# floor was met by junk.
COPYFILE_DISABLE=1 tar -C "${REPO_DIR}" \
	--exclude='.git' \
	--exclude='__pycache__' \
	--exclude='*.pyc' \
	--exclude='._*' \
	--exclude='.DS_Store' \
	-cf - . \
	| host_cmd "docker exec -i -u root ${CONTAINER} tar -C ${APP_DIR} -xf -"

echo "==> Fixing ownership"
host_cmd "docker exec -u root ${CONTAINER} bash -lc 'chown -R frappe:frappe ${APP_DIR}'"

echo "==> Registering app with bench"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc '
	cd ${BENCH_DIR} &&
	if bench get-app ${APP_DIR}; then
		echo \"get-app succeeded\";
	else
		echo \"get-app failed, falling back to pip install -e\";
		env/bin/pip install -e apps/sparsh_los;
		# apps.txt may have no trailing newline; appending blind fuses the last
		# app name onto sparsh_los and the import then fails on a merged module.
		grep -qx sparsh_los sites/apps.txt || {
			[ -s sites/apps.txt ] && [ -n \"\$(tail -c 1 sites/apps.txt)\" ] && echo >> sites/apps.txt;
			echo sparsh_los >> sites/apps.txt;
		};
	fi
'"

echo "==> Installing app on site ${SITE}"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} install-app sparsh_los'"

echo "==> Migrating (DocType JSON changes only reach the database through this)"
# install-app is a no-op when the app is already installed, so a changed DocType JSON
# never reached the schema and a new column silently did not exist. Bumping `modified`
# is necessary and was never sufficient on its own.
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} migrate'"

echo "==> Clearing cache"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} clear-cache'"

echo "==> App list"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} list-apps | grep sparsh_los'"

echo "==> DocType count for module 'Sparsh LOS'"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc \"cd ${BENCH_DIR} && bench --site ${SITE} mariadb --execute=\\\"select count(*) from tabDocType where module='Sparsh LOS' and istable=0\\\"\""

if [ "${SKIP_VERIFY}" != "1" ]; then
	echo "==> Running behavioural verification harness"
	OUTPUT="$(host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} execute sparsh_los.verify.run'")"
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
