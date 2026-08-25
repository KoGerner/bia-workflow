#!/usr/bin/env python3
"""Print the top section of CHANGELOG.md.

Stdlib only and no venv: this runs inside a public GitHub runner as well as from release.sh.
Both call it, so the Release body and the file can never disagree about where a section ends.
"""
import argparse, sys


def top_section(text):
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## ")), None)
    if start is None:
        return ""
    end = next((i for i, l in enumerate(lines[start + 1:], start + 1)
                if l.startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file")
    ap.add_argument("--expect", help="fail unless the heading names this version")
    a = ap.parse_args()
    text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
    section = top_section(text)
    if not section:
        print("no '## ' section found", file=sys.stderr)
        raise SystemExit(1)
    if a.expect and a.expect not in section.splitlines()[0]:
        print(f"top section is {section.splitlines()[0]!r}, expected {a.expect}", file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.write(section)


if __name__ == "__main__":
    main()
