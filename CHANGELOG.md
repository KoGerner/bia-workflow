# Changelog

Release history starts at v0.1.0 (2026-08-25). The repository went public on 2026-08-20;
changes between that date and the first release are not covered here, because the release
process did not exist yet.

Each entry is written for someone running a BIA, not for someone reading a diff.

These numbers are the project's, not the server's. `/health` reports `SERVER_VERSION`, which
describes the MCP surface's API contract and moves for its own reasons; the two are allowed to
differ and currently do.

## v0.1.0

### Summary

First tagged release. Everything in it is in the surface you actually talk to: the assistant
opens by asking what it already knows about your company and which process to start with
rather than greeting you, and it now stays in the room it started in instead of following a
room code you mention in passing.

### Other changes

- The demo agent's first message is a question, not a greeting. It asks what the assistant
  already knows about your company and which process you would start with, so the opening
  turn comes back with a ranked starting point and the reasons behind it. This costs roughly
  one extra turn before the interview proper begins.
- The separate "Hello, I'm BIA-Workflow" line is gone from pages that already open with a
  question. It still appears where nothing is sent automatically.
- A conversation stays in the room it started in. Naming a different room code mid-way no
  longer silently switches to it: the assistant says the code belongs to someone else's work
  and offers to carry on there instead. Rooms remain readable by anyone holding the code —
  this is a guardrail against wandering, not access control.
- Demo room codes are unguessable names instead of a running sequence. Anyone claiming a room
  by QR code sees no difference; a code you were given before this release will not resolve.
- Activity lists render as lists. They previously arrived run together in a single paragraph.
