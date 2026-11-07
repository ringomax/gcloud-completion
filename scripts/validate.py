#!/usr/bin/env python3
"""Verify every source command and flag against generated Fig modules."""
import argparse
import json
from pathlib import Path

from generate import TREE_PATH, load_tree, sdk_root


def read_spec(path):
    return json.loads(path.read_text().split("export default ", 1)[1].removesuffix(";\n"))


def validate(source, directory):
    index = read_spec(directory / "index.js")
    if len(index) != len(set(index)):
        raise AssertionError("Duplicate module names")
    modules = {name: read_spec(directory / (name + ".js")) for name in index}
    visited = set()
    nodes = 0

    def walk(node, spec, path):
        nonlocal nodes
        nodes += 1
        options = {o["name"]: o for o in spec.get("options", [])}
        assert set(options) == set(node["flags"]), "Flag mismatch at " + path
        for name, value in node["flags"].items():
            option = options[name]
            assert option["isPersistent"]
            assert ("args" in option) == (value != "bool"), (path, name)
            if isinstance(value, list):
                assert option["args"]["suggestions"] == [str(v) for v in value], (path, name)
        children = {c["name"]: c for c in spec.get("subcommands", [])}
        assert set(children) == set(node["commands"]), "Command mismatch at " + path
        for name, child in node["commands"].items():
            converted = children[name]
            if "loadSpec" in converted:
                key = converted["loadSpec"]
                assert key in modules, "Missing module " + key
                visited.add(key)
                converted = modules[key]
            walk(child, converted, path + " " + name)

    visited.add("gcloud")
    walk(source, modules["gcloud"], "gcloud")
    assert set(modules) == visited, "Unreachable generated modules"
    return nodes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--specs", type=Path, default=Path(__file__).resolve().parents[1] / "specs")
    args = parser.parse_args()
    print("Validated", validate(load_tree((args.sdk_root or sdk_root()) / TREE_PATH), args.specs), "nodes")
