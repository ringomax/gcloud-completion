#!/usr/bin/env python3
"""Convert the completion tree shipped with Google Cloud CLI to Fig specs."""
import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

SCHEMA = 1
TREE_PATH = Path("data/cli/gcloud_completions.py")
NAME = re.compile(r"^[a-zA-Z0-9_.+-]+$")


def sdk_root(executable="gcloud"):
    binary = shutil.which(executable)
    if not binary:
        raise RuntimeError("gcloud is not on PATH")
    candidate = Path(binary).resolve().parent.parent
    if (candidate / TREE_PATH).is_file() and (candidate / "VERSION").is_file():
        return candidate
    output = subprocess.check_output(
        [binary, "info", "--format=value(installation.sdk_root)"], text=True, timeout=30)
    candidate = Path(output.strip()).resolve()
    if not (candidate / TREE_PATH).is_file():
        raise RuntimeError("Google Cloud CLI's bundled static completion tree is missing")
    return candidate


def load_tree(source):
    # Parse the SDK data without executing Python from the SDK installation.
    module = ast.parse(source.read_text())
    assignments = [n for n in module.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "STATIC_COMPLETION_CLI_TREE" for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError("Unsupported SDK tree: expected STATIC_COMPLETION_CLI_TREE")
    tree = ast.literal_eval(assignments[0].value)
    validate_tree(tree)
    return tree


def validate_tree(node):
    if not isinstance(node, dict) or set(node) != {"commands", "flags"}:
        raise ValueError("Unsupported SDK completion tree schema")
    if not isinstance(node["commands"], dict) or not isinstance(node["flags"], dict):
        raise ValueError("Invalid command/flag dictionaries")
    for name, value in node["flags"].items():
        if not name.startswith("--") or not NAME.fullmatch(name):
            raise ValueError("Invalid flag name")
        if not (isinstance(value, str) and value in ("bool", "value", "dynamic")
                or isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value)):
            raise ValueError("Unsupported flag metadata for " + name)
    for name, child in node["commands"].items():
        if not NAME.fullmatch(name):
            raise ValueError("Invalid command name")
        validate_tree(child)


def encode(spec):
    return "// Generated from Google Cloud CLI; see NOTICE.\nexport default " + json.dumps(spec, separators=(",", ":"), ensure_ascii=False) + ";\n"


def build(tree):
    modules = {}
    counts = {"nodes": 0, "commands": 0, "flags": 0}

    def convert(node, path):
        counts["nodes"] += 1
        counts["commands"] += not bool(node["commands"])
        counts["flags"] += len(node["flags"])
        spec = {"name": path[-1]}
        options = []
        for name, value in sorted(node["flags"].items()):
            option = {"name": name, "isPersistent": True}
            if value != "bool":
                option["args"] = {"name": name[2:].upper().replace("-", "_")}
                if isinstance(value, list):
                    option["args"]["suggestions"] = [str(v) for v in value]
                elif name.endswith("-file"):
                    option["args"]["template"] = "filepaths"
            options.append(option)
        if options:
            spec["options"] = options
        children = []
        for name, child in sorted(node["commands"].items()):
            child_path = path + [name]
            converted = convert(child, child_path)
            # Load each product independently, including alpha/beta products.
            split = len(path) == 1 or len(path) == 2 and path[1] in ("alpha", "beta")
            if split:
                key = "/".join(child_path)
                modules[key] = converted
                children.append({"name": name, "loadSpec": key})
            else:
                children.append(converted)
        if children:
            spec["subcommands"] = children
        else:
            # Static SDK data does not describe positional arguments. Accept
            # them so flags after a resource name remain completable.
            spec["args"] = {"name": "ARGUMENT", "isOptional": True, "isVariadic": True}
        return spec

    modules["gcloud"] = convert(tree, ["gcloud"])
    return modules, counts


def generate(sdk, output):
    source = sdk / TREE_PATH
    version = (sdk / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid SDK version")
    modules, counts = build(load_tree(source))
    output.mkdir(parents=True, exist_ok=True)
    for name, spec in sorted(modules.items()):
        target = output / (name + ".js")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encode(spec))
    names = sorted(modules)
    (output / "index.js").write_text("export default " + json.dumps(names, separators=(",", ":")) + ";\n")
    (output / "package.json").write_text('{"type":"module"}\n')
    metadata = {"sdk_version": version, "generator_schema": SCHEMA,
                "source": TREE_PATH.as_posix(), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "modules": len(names), **counts}
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "specs")
    args = parser.parse_args()
    if args.output.exists():
        # Only replace this tool's generated output, never an arbitrary directory.
        if not (args.output / "metadata.json").is_file() and any(args.output.iterdir()):
            raise SystemExit("Refusing to overwrite a directory without metadata.json")
        shutil.rmtree(args.output)
    print(json.dumps(generate((args.sdk_root or sdk_root()).resolve(), args.output.resolve()), indent=2))
