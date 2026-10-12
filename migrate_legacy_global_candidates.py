"""Explicit operator CLI; stdout is an auditable technical JSON report."""
from __future__ import annotations

import argparse
import json
import sys

from legacy_global_migration import migrate_project


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate/import non-reusable legacy glyph candidates")
    parser.add_argument("--project-output", required=True)
    parser.add_argument("--pdf-root", action="append", default=[])
    parser.add_argument("--global-library-root", help="Explicit library directory (use temporary roots for tests)")
    parser.add_argument("--apply", action="store_true", help="Explicitly commit to Global SQLite")
    args = parser.parse_args(argv)
    try:
        result = migrate_project(args.project_output, pdf_roots=args.pdf_root,
                                 global_library_root=args.global_library_root, apply=args.apply)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
