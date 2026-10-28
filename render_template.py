"""Render app.py into the CloudFormation ZipFile field using only Python stdlib."""
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
template = json.loads((root / "template.json").read_text(encoding="utf-8"))
template["Resources"]["OrderFunction"]["Properties"]["Code"]["ZipFile"] = (root / "app.py").read_text(encoding="utf-8")
output = pathlib.Path(sys.argv[1])
output.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
print(f"Rendered {output}")
