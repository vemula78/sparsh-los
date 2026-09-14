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
MIN_CHECKS="${MIN_CHECKS:-158}"

case "${TARGET}" in
local)
	SITE="${SITE:-sparsh.localhost}"
	CONTAINER="${CONTAINER:-frappe_docker-backend-1}"
	FRONTEND="${FRONTEND:-frappe_docker-frontend-1}"
	;;
remote)
	SITE="${SITE:-}"
	CONTAINER="${CONTAINER:-internal-backend-1}"
	FRONTEND="${FRONTEND:-internal-frontend-1}"
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

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="/home/frappe/frappe-bench"
# Where the frontend image actually keeps served assets. Not under `sites`.
FRONTEND_ASSETS="${FRONTEND_ASSETS:-/home/frappe/frappe-bench/assets}"
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

echo "==> Linking public assets"
# The pages load /assets/sparsh_los/css/sparsh.css, which is served from a symlink under
# sites/assets. Nothing in install-app or migrate creates it, so a fresh bench served
# every page unstyled and the CSS 404'd in silence -- a stylesheet that fails to load
# looks like a page somebody never designed.
#
# `bench build --app sparsh_los` would create it, then exit non-zero on this stack because
# node is not installed, which would fail the whole install for a step that had already
# succeeded. The link is made directly instead: it needs no toolchain and is idempotent.
# The assets have to land in the FRONTEND container, and this took three wrong attempts
# to establish. `sites` is a shared volume, so the obvious `ln -sfn` into
# sites/assets/sparsh_los looked right from the backend -- the file was there, readable,
# correct. But in the frontend image `sites/assets` is itself a symlink to
# `/home/frappe/frappe-bench/assets`, which lives in the image layer and not in any
# volume. nginx serves from there, so nothing written to the volume is ever served, and
# copying instead of linking did not help either. A stylesheet that 404s renders as a
# page nobody styled rather than as an error, which is why it went unnoticed.
#
# This is a deploy-time copy into a container's own filesystem, so it does not survive
# the frontend being recreated. The durable fix is to bake the app into the frontend
# image; until that happens, re-running this script restores it.
host_cmd "docker exec ${CONTAINER} tar -C ${APP_DIR}/sparsh_los -cf - public" \
	| host_cmd "docker exec -u root -i ${FRONTEND} bash -lc '
		rm -rf ${FRONTEND_ASSETS}/sparsh_los &&
		rm -rf /tmp/_sparsh_assets && mkdir -p /tmp/_sparsh_assets &&
		tar -C /tmp/_sparsh_assets -xf - &&
		mv /tmp/_sparsh_assets/public ${FRONTEND_ASSETS}/sparsh_los &&
		chown -R frappe:frappe ${FRONTEND_ASSETS}/sparsh_los &&
		rm -rf /tmp/_sparsh_assets
	'"

echo "==> Reloading the application workers"
# Without this the deploy is not finished, however green the harness is.
#
# gunicorn workers are long-lived processes. They hold the modules they imported at
# boot and the website route map they built then, so a `www` page added by this deploy
# resolves to its template while its controller module is never imported. Jinja then
# renders the template against an empty context and the page 500s on the first variable
# it touches -- `'branding' is undefined` -- with nothing written to the Error Log.
#
# The harness cannot catch it. `run_verify.py` starts a fresh interpreter, where the
# import always succeeds, so a page that is dead on the server verifies perfectly. Two
# new pages were served broken while 158 checks passed.
#
# HUP, not a container restart: the master re-execs its workers and the container keeps
# running, so the other seventeen apps on this bench are not interrupted.
host_cmd "docker kill -s HUP ${CONTAINER}" >/dev/null
sleep 5

echo "==> Clearing cache"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} clear-cache'"

echo "==> App list"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR} && bench --site ${SITE} list-apps | grep sparsh_los'"

echo "==> DocType count for module 'Sparsh LOS'"
host_cmd "docker exec -u frappe ${CONTAINER} bash -lc \"cd ${BENCH_DIR} && bench --site ${SITE} mariadb --execute=\\\"select count(*) from tabDocType where module='Sparsh LOS' and istable=0\\\"\""

if [ "${SKIP_VERIFY}" != "1" ] && [ "${TARGET}" = "local" ]; then
	# No rq worker on the local compose stack, so enqueued jobs accumulate until the
	# framework refuses to enqueue at all. At 140+ checks a single run reaches the cap
	# by itself, so the flush belongs here rather than in the operator's memory.
	# Local only -- never the hospital bench, which has real workers and real jobs.
	docker exec frappe_docker-redis-queue-1 redis-cli flushall >/dev/null 2>&1 || true
fi

if [ "${SKIP_VERIFY}" != "1" ]; then
	echo "==> Running behavioural verification harness"
	# Run through the bench's own python rather than `bench execute`. `bench execute`
	# falls back to `eval()` on the method string, so ANY exception raised while
	# importing or running the harness is reported as
	# `NameError: name 'sparsh_los' is not defined` -- the real error never appears.
	# That cost hours twice: once on a QueueOverloaded from this stack's missing rq
	# worker, once on a genuine harness failure that looked identical. The redis flush
	# below removes the usual cause; this removes the masking.
	# `|| true` is load-bearing under `set -e`. The harness exits non-zero when a check
	# fails, and without it the shell killed the script at this assignment -- before the
	# `echo` below, before every diagnostic. A failing run printed the banner and nothing
	# else, which read as the harness hanging rather than as checks failing. It cost two
	# separate investigations on one day. The exit status is not lost: it is re-derived
	# from the RESULT line, which is the thing actually worth trusting.
	OUTPUT="$(host_cmd "docker exec -u frappe ${CONTAINER} bash -lc 'cd ${BENCH_DIR}/sites && ../env/bin/python ${APP_DIR}/scripts/run_verify.py ${SITE} 2>&1'" || true)"
	echo "${OUTPUT}"
	if ! echo "${OUTPUT}" | grep -q 'RESULT passed='; then
		echo "FAIL: verify harness did not print a RESULT line" >&2
		exit 1
	fi
	# An empty CHECKS tuple would print "passed=0 failed=0" and look like success.
	PASSED=$(echo "${OUTPUT}" | sed -n 's/.*RESULT passed=\([0-9]*\).*/\1/p')
	# A check that could not run here is counted, named in the output above, and added
	# back before comparing against the floor -- otherwise loading the demonstration
	# cohort, which legitimately makes two window-dependent checks inapplicable, would
	# read as two checks having gone missing. What must never be tolerated is `failed`,
	# which is asserted separately below.
	SKIPPED=$(echo "${OUTPUT}" | sed -n 's/.*RESULT .*skipped=\([0-9]*\).*/\1/p')
	RAN=$(( ${PASSED:-0} + ${SKIPPED:-0} ))
	if [ "${RAN}" -lt "${MIN_CHECKS:-1}" ]; then
		echo "FAIL: only ${RAN} checks accounted for (${PASSED:-0} passed, ${SKIPPED:-0} skipped), expected at least ${MIN_CHECKS:-1}" >&2
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
