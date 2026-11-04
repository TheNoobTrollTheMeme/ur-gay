#!/usr/bin/env python3
"""Offline JSON validation and point inspection. No servo imports or hardware I/O."""
import json
import re
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
motions=root/"motions"
manifest=json.loads((root/"calibration_status.json").read_text(encoding="utf-8"))
assert not manifest["all_hardware_approved"], "Safety status must remain false unless explicitly reviewed"
expected={f"c{c}r{r}{phase}" for c in (1,2,3) for r in (1,2,3) for phase in "mp"}
found={p.stem for p in motions.glob("*.json")}
assert found==expected,(sorted(expected-found),sorted(found-expected))
for name in sorted(found):
    obj=json.loads((motions/(name+".json")).read_text(encoding="utf-8"))
    assert obj["name"]==name and obj["type"]=="pose_sequence" and obj["input_type"]=="raw"
    assert len(obj["points"])==2 and len(obj["joint_order"])==5
    assert not manifest["motions"][name]["hardware_approved"], name
    for item in obj["points"]:
        vals=[int(x) for x in item["input_values"]]
        assert len(vals)==5 and all(0<=v<=4095 for v in vals),(name,vals)
print("PASS: 18 motion JSONs; two waypoints per file; servo ticks within 0-4095.")
print("WARNING: numerical validity DOES NOT imply physical safety; NO files are hardware-approved.")
if len(sys.argv)>1:
    name=sys.argv[1]
    if not re.fullmatch(r"c[123]r[123][mp]",name): raise SystemExit("Expected e.g. c1r1m")
    obj=json.loads((motions/(name+".json")).read_text(encoding="utf-8"))
    print(name,manifest["motions"][name])
    for i,p in enumerate(obj["points"],1): print(f" waypoint {i}:",p["input_values"])
