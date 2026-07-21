#!/usr/bin/env python3
"""
yaml_key_diff.py — structural key-path diff between a freshly-built default
Open5GS config (build/configs/open5gs/<nf>.yaml) and the deployed, hand-
customized config (install/etc/open5gs/<nf>.yaml).

Open5GS's meson install script never overwrites an existing config file
(see install_conf in meson.build), so a `git pull` + rebuild that adds new
mandatory keys to configs/open5gs/<nf>.yaml.in will NOT propagate into the
deployed config. This script surfaces that drift as a key-path diff instead
of a raw line diff, so address/log-level/etc. customizations don't drown
out the keys that actually matter.

Usage: yaml_key_diff.py <new_template.yaml> <deployed.yaml>
Exit codes: 0 = no new/removed keys (value-only or no changes)
            1 = new and/or removed keys found (surfaced, not applied)
            2 = usage / load error
"""
import sys
import yaml


def flatten(obj, prefix=""):
    """Yield (key_path, value) for every scalar/leaf in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield from flatten(v, path)
    elif isinstance(obj, list):
        if not obj:
            yield (prefix, obj)
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield (prefix, obj)


def load(path):
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    new_path, deployed_path = sys.argv[1], sys.argv[2]
    try:
        new_cfg = dict(flatten(load(new_path)))
        old_cfg = dict(flatten(load(deployed_path)))
    except Exception as exc:
        print(f"ERROR: could not parse YAML — {exc}", file=sys.stderr)
        return 2

    new_keys = sorted(set(new_cfg) - set(old_cfg))
    removed_keys = sorted(set(old_cfg) - set(new_cfg))
    changed_keys = sorted(
        k for k in (set(new_cfg) & set(old_cfg))
        if new_cfg[k] != old_cfg[k]
    )

    # List-length changes produce noisy [i] paths (e.g. a new sbi.server
    # entry). Collapse those to one line per list root instead of one per
    # index so the report stays readable.
    def collapse_list_paths(keys):
        seen = {}
        for k in keys:
            root = k.split("[", 1)[0]
            seen.setdefault(root, 0)
            seen[root] += 1
        return seen

    if not new_keys and not removed_keys:
        print(f"  {deployed_path}: no structural key differences "
              f"({len(changed_keys)} value-only change(s))")
        return 0

    print(f"  {deployed_path}")
    if new_keys:
        print(f"    NEW keys upstream (not in deployed config):")
        for root, count in collapse_list_paths(new_keys).items():
            suffix = f"  (x{count} list entries)" if count > 1 else ""
            print(f"      + {root}{suffix}")
    if removed_keys:
        print(f"    Keys in deployed config absent from the fresh template")
        print(f"    (often a commented-out-by-default key you've deliberately turned on —")
        print(f"     e.g. logger.level — but check for deprecated/renamed keys too):")
        for root, count in collapse_list_paths(removed_keys).items():
            suffix = f"  (x{count} list entries)" if count > 1 else ""
            print(f"      - {root}{suffix}")
    if changed_keys:
        print(f"    {len(changed_keys)} value-only difference(s) (expected — your customizations)")

    return 1


if __name__ == "__main__":
    sys.exit(main())
