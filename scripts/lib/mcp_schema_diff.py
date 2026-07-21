#!/usr/bin/env python3
"""
mcp_schema_diff.py — compare two tools/list snapshots (raw JSON-RPC
`result.tools` arrays) and flag changes that would break an existing caller.

Usage: mcp_schema_diff.py <old_snapshot.json> <new_snapshot.json>
Exit codes: 0 = no breaking changes
            1 = breaking change(s) found
            2 = usage / load error
"""
import json
import sys


def load_tools(path):
    with open(path) as f:
        data = json.load(f)
    return {t["name"]: t for t in data}


def param_props(tool):
    schema = tool.get("inputSchema", {})
    return schema.get("properties", {}), set(schema.get("required", []))


def type_of(prop):
    if "enum" in prop:
        return "enum(" + "|".join(sorted(str(x) for x in prop["enum"])) + ")"
    if "type" in prop:
        return prop["type"]
    if "anyOf" in prop:
        return "anyOf(" + "|".join(sorted(x.get("type", "?") for x in prop["anyOf"])) + ")"
    return "any"


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    try:
        old = load_tools(sys.argv[1])
        new = load_tools(sys.argv[2])
    except Exception as exc:
        print(f"ERROR: could not load snapshot — {exc}", file=sys.stderr)
        return 2

    breaking = []
    notes = []

    removed_tools = sorted(set(old) - set(new))
    added_tools = sorted(set(new) - set(old))
    for name in removed_tools:
        breaking.append(f"tool removed: '{name}' — any caller invoking it will now fail")
    for name in added_tools:
        notes.append(f"tool added: '{name}' (non-breaking)")

    for name in sorted(set(old) & set(new)):
        old_props, old_req = param_props(old[name])
        new_props, new_req = param_props(new[name])

        removed_params = set(old_props) - set(new_props)
        added_required = (set(new_props) - set(old_props)) & new_req
        newly_required = (set(old_props) & set(new_props)) & (new_req - old_req)

        for p in sorted(removed_params):
            sev = "required" if p in old_req else "optional"
            if sev == "required":
                breaking.append(f"{name}: {sev} param '{p}' was removed — existing calls that pass it will now fail schema validation")
            else:
                notes.append(f"{name}: {sev} param '{p}' was removed (non-breaking unless callers relied on it)")

        for p in sorted(added_required):
            breaking.append(f"{name}: new REQUIRED param '{p}' — existing callers that don't pass it will now fail")

        for p in sorted(newly_required):
            breaking.append(f"{name}: param '{p}' changed from optional to required — existing callers that omit it will now fail")

        for p in sorted(set(old_props) & set(new_props)):
            old_t, new_t = type_of(old_props[p]), type_of(new_props[p])
            if old_t != new_t:
                breaking.append(f"{name}: param '{p}' type changed ({old_t} -> {new_t})")

    print(f"Compared {len(old)} -> {len(new)} tools "
          f"({len(added_tools)} added, {len(removed_tools)} removed).\n")

    if breaking:
        print(f"BREAKING changes ({len(breaking)}):")
        for b in breaking:
            print(f"  ✗ {b}")
        print()
    if notes:
        print(f"Non-breaking changes ({len(notes)}):")
        for n in notes:
            print(f"  · {n}")
        print()
    if not breaking and not notes:
        print("No differences in tool names or parameter schemas.")

    return 1 if breaking else 0


if __name__ == "__main__":
    sys.exit(main())
