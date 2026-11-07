#!/usr/bin/env python3
"""Refresh Figxit's gcloud specs from the installed SDK when its version changes."""
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from generate import SCHEMA, TREE_PATH, generate, sdk_root

ROOT = Path(__file__).resolve().parents[1]


def atomic_text(path, text):
    if path.is_file() and path.read_text() == text:
        return False
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(text)
    temporary.replace(path)
    return True


def atomic_link(path, target):
    if path.is_symlink() and os.readlink(path) == str(target):
        return False
    if path.exists() and not path.is_symlink():
        raise ValueError("Refusing to overwrite unmanaged path: " + str(path))
    temporary = path.with_name(path.name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(path)
    return True


def overlay(app, destination):
    bundled = app / "Contents/Resources/specs"
    if not (bundled / "index.js").is_file():
        raise ValueError("Figxit's bundled index.js is missing")
    custom = destination / "custom"
    target = destination / "specs"
    custom.mkdir(exist_ok=True)
    target.mkdir(exist_ok=True)
    current = destination / "gcloud-current"
    for name in ("gcloud.js", "gcloud"):
        path = custom / name
        if path.exists() and not path.is_symlink():
            backup = destination / ("legacy-gcloud-" + str(time.time_ns()))
            backup.mkdir()
            shutil.move(str(path), str(backup / name))
        atomic_link(path, current / name)
    changed = False
    for source in (bundled, custom):
        for item in source.iterdir():
            if item.name in ("index.js", "package.json"):
                continue
            changed |= atomic_link(target / item.name, item)
    # os.walk follows custom spec directories that are symlinked to a generation.
    names = []
    for folder, _, files in os.walk(custom, followlinks=True):
        for filename in files:
            if filename.endswith(".js") and filename != "index.js":
                names.append((Path(folder) / filename).relative_to(custom).with_suffix("").as_posix())
    index = ("import bundled from " + json.dumps((bundled / "index.js").as_uri()) + ";\n"
             "export default [...new Set([...bundled,..." + json.dumps(sorted(names), separators=(",", ":")) + "])];\n")
    changed |= atomic_text(target / "index.js", index)
    changed |= atomic_text(target / "package.json", '{"type":"module"}\n')
    return changed


def refresh(sdk, app, destination):
    destination.mkdir(parents=True, exist_ok=True)
    lock = (destination / "gcloud-sync.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    try:
        source = sdk / TREE_PATH
        fingerprint = {
            "sdk_version": (sdk / "VERSION").read_text().strip(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "generator_sha256": hashlib.sha256((ROOT / "scripts/generate.py").read_bytes()).hexdigest(),
            "generator_schema": SCHEMA,
        }
        current = destination / "gcloud-current"
        previous = {}
        try:
            previous = json.loads((current / "metadata.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        changed = not all(previous.get(k) == v for k, v in fingerprint.items())
        if changed:
            generations = destination / "gcloud-generations"
            generations.mkdir(exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
            try:
                metadata = generate(sdk, staging)
                metadata.update(fingerprint)
                (staging / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
                shutil.copy2(ROOT / "NOTICE", staging / "NOTICE")
                shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
                final = generations / (fingerprint["sdk_version"] + "-" + str(time.time_ns()))
                staging.rename(final)
                atomic_link(current, final)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        changed |= overlay(app, destination)
        return changed
    finally:
        lock.close()


def restart(app, destination):
    specs = str(destination / "specs")
    subprocess.run(["launchctl", "setenv", "FIGXIT_SPECS", specs], check=True)
    subprocess.run([str(app / "Contents/MacOS/figxit-engine"), "stop"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["/usr/bin/open", "-g", "-a", str(app), "--env", "FIGXIT_SPECS=" + specs], check=True)


def hook_text(app, destination, sdk, interpreter):
    command = " ".join(shlex.quote(str(p)) for p in
                       (interpreter, ROOT / "scripts/sync.py", "--app", app, "--destination", destination))
    return """# Generated by gcloud-completion. Source this before Figxit's shell init.
export FIGXIT_SPECS=%(specs)s
_figxit_gcloud_refresh() {
  local _fgc_bin _fgc_sdk _fgc_version _fgc_key _fgc_link
  _fgc_bin=$(command -v gcloud) || return 0
  case "$_fgc_bin" in
    /*) ;;
    *) return 0 ;;
  esac
  while [ -L "$_fgc_bin" ]; do
    _fgc_link=$(readlink "$_fgc_bin") || return 0
    case "$_fgc_link" in
      /*) _fgc_bin=$_fgc_link ;;
      *) _fgc_bin="${_fgc_bin%%/*}/$_fgc_link" ;;
    esac
  done
  _fgc_sdk=${_fgc_bin%%/*}
  _fgc_sdk=${_fgc_sdk%%/*}
  if [ ! -f "$_fgc_sdk/VERSION" ]; then
    case "$_fgc_bin" in
      */shims/gcloud) _fgc_sdk=$(mise where gcloud 2>/dev/null) || return 0 ;;
      *) _fgc_sdk=%(sdk)s ;;
    esac
  fi
  [ -r "$_fgc_sdk/VERSION" ] || return 0
  IFS= read -r _fgc_version < "$_fgc_sdk/VERSION"
  _fgc_key="$_fgc_bin:$_fgc_sdk:$_fgc_version"
  [ "${_figxit_gcloud_seen-}" = "$_fgc_key" ] && return 0
  _figxit_gcloud_seen=$_fgc_key
  %(command)s --sdk-root "$_fgc_sdk" >> %(log)s 2>&1 ||
    printf 'gcloud completion update failed; see %%s\\n' %(log)s >&2
  return 0
}
_figxit_gcloud_refresh
if [ -n "${ZSH_VERSION-}" ]; then
  case " ${precmd_functions[*]-} " in
    *" _figxit_gcloud_refresh "*) ;;
    *) precmd_functions+=(_figxit_gcloud_refresh) ;;
  esac
fi
""" % {"specs": shlex.quote(str(destination / "specs")), "sdk": shlex.quote(str(sdk)),
       "command": command, "log": shlex.quote(str(destination / "gcloud-sync.log"))}


def install_hooks(home, app, destination, sdk, interpreter):
    hook = destination / "gcloud-sync.sh"
    atomic_text(hook, hook_text(app, destination, sdk, interpreter))
    line = "[ ! -f " + shlex.quote(str(hook)) + " ] || . " + shlex.quote(str(hook))
    for filename in (".zshrc", ".profile"):
        path = home / filename
        text = path.read_text() if path.exists() else ""
        if line in text:
            continue
        marker = "# Figxit\n"
        addition = "# gcloud-completion automatic version sync\n" + line + "\n"
        updated = text.replace(marker, addition + marker, 1) if marker in text else text + "\n" + addition
        backup = destination / (filename.lstrip(".") + ".before-gcloud-sync")
        if not backup.exists() and path.exists():
            # Backups can contain personal shell settings; keep them local.
            shutil.copy2(path, backup)
            backup.chmod(0o600)
        path.write_text(updated)
    label = "local.figxit.custom-specs"
    agent = home / "Library/LaunchAgents" / (label + ".plist")
    agent.parent.mkdir(parents=True, exist_ok=True)
    content = plistlib.dumps({"Label": label, "RunAtLoad": True,
        "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(app), "--env", "FIGXIT_SPECS=" + str(destination / "specs")]})
    if not agent.exists() or agent.read_bytes() != content:
        subprocess.run(["launchctl", "bootout", "gui/" + str(os.getuid()) + "/" + label], capture_output=True)
        agent.write_bytes(content)
        subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(agent)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--app", type=Path, default=Path("/Applications/Figxit.app"))
    parser.add_argument("--destination", type=Path, default=Path.home() / "Library/Application Support/figxit-custom")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    sdk = (args.sdk_root or sdk_root()).resolve()
    destination = args.destination.resolve()
    app = args.app.resolve()
    changed = refresh(sdk, app, destination)
    if args.install:
        install_hooks(Path.home(), app, destination, sdk, "/usr/bin/python3")
    if changed and not args.no_restart:
        restart(app, destination)
    print("Updated gcloud completion" if changed else "gcloud completion is current")
