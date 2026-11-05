# Offline validation report — 2026-11-05

23 tests passed in the separate `safety_workcopy/motion_audit_validation`
copy (0.531 seconds), using `python -m unittest discover -s tests -v`.
The same 23 tests also passed from the repository root (0.743 seconds).

The new integration test checks all 18 byte-exact backups and candidates,
two-point preservation, unchanged source hashes, known travel arithmetic,
all 27 preflight rejections with zero commands, absence of invented speed
settings, and refusal to overwrite an existing audit. Existing simulator tests
exercise synthetic collision coverage, jams, timeouts, feedback faults and stops.
These are software checks, not validation of physical trajectories.

Audit generated from the separate input copy with:

```powershell
python -m simulation_safety.audit_motions --source . --output ../../motion_audit
```

Run from the repository root to reproduce into a fresh directory instead:

```powershell
python -m simulation_safety.audit_motions --source . --output safety_workcopy/reproduced_audit
```

Git comparison confirms the live script and original 18 motion files are
unchanged. Backups and candidate files retain the original bytes; candidate
hashes in `report.json` match their source hashes.

No hardware tests, driver imports, servo-port access or physical commands.
No trajectory is cleared for hardware. Maximum safe speed, actual orientation,
clearance and dynamics remain unvalidated for the reasons in `REPORT.md`.
