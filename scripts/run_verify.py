#!/usr/bin/env python
"""Run the behavioural harness without `bench execute` masking the error.

`bench execute` evaluates the method string and falls back to `eval()`, so anything
raised while importing or running the harness is reported as
`NameError: name 'sparsh_los' is not defined` and the real error never appears. That
cost hours twice -- once on a QueueOverloaded from this stack's missing rq worker,
once on a genuine harness failure that looked exactly the same.

Usage: python scripts/run_verify.py <site>
"""

import sys

import frappe


def main():
	if len(sys.argv) < 2:
		raise SystemExit("usage: run_verify.py <site>")

	site = sys.argv[1]
	frappe.init(site=site)
	frappe.connect()
	try:
		from sparsh_los import verify

		verify.run()
	finally:
		frappe.destroy()


if __name__ == "__main__":
	main()
