import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate
import sync
import validate


TREE = {"commands": {
    "run": {"commands": {"deploy": {"commands": {}, "flags": {
        "--image": "value", "--async": "bool", "--project": "dynamic",
        "--ingress": ["all", "internal"], "--env-vars-file": "value"}}}, "flags": {}},
    "beta": {"commands": {"run": {"commands": {"worker-pools": {"commands": {
        "create": {"commands": {}, "flags": {"--image": "value"}}}, "flags": {}}}, "flags": {}}}, "flags": {}},
    "alpha": {"commands": {}, "flags": {}},
}, "flags": {"--project": "dynamic", "--help": "bool"}}


def make_sdk(root, tree=TREE, version="590.0.1"):
    root.mkdir(exist_ok=True)
    (root / "VERSION").write_text(version + "\n")
    source = root / generate.TREE_PATH
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("STATIC_COMPLETION_CLI_TREE = " + repr(tree) + "\n")
    (root / "bin").mkdir(exist_ok=True)
    binary = root / "bin/gcloud"
    binary.write_text("#!/bin/sh\nexit 99\n")
    binary.chmod(0o755)
    return root


class CompletionTest(unittest.TestCase):
    def test_every_command_flag_type_enum_and_lazy_module_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sdk = make_sdk(root / "sdk")
            output = root / "specs"
            info = generate.generate(sdk, output)
            self.assertEqual(validate.validate(TREE, output), info["nodes"])
            deploy = validate.read_spec(output / "gcloud/run.js")["subcommands"][0]
            options = {o["name"]: o for o in deploy["options"]}
            self.assertNotIn("args", options["--async"])
            self.assertEqual(options["--ingress"]["args"]["suggestions"], ["all", "internal"])
            self.assertEqual(options["--env-vars-file"]["args"]["template"], "filepaths")
            self.assertTrue((output / "gcloud/beta/run.js").is_file())

    def test_sdk_python_data_is_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "tree.py"
            sentinel = Path(folder) / "executed"
            source.write_text("open(" + repr(str(sentinel)) + ",'w').write('bad')\nSTATIC_COMPLETION_CLI_TREE = " + repr(TREE))
            self.assertEqual(generate.load_tree(source), TREE)
            self.assertFalse(sentinel.exists())

    def test_updates_preserve_git_pnpm_acloud_and_keep_last_good_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sdk = make_sdk(root / "sdk")
            app = root / "Figxit.app"
            bundle = app / "Contents/Resources/specs"
            bundle.mkdir(parents=True)
            (bundle / "index.js").write_text('export default ["git","pnpm"];\n')
            for name in ("git", "pnpm"):
                (bundle / (name + ".js")).write_text('export default {"name":"' + name + '"};\n')
            destination = root / "custom"
            (destination / "custom").mkdir(parents=True)
            acloud = destination / "custom/acloud.js"
            acloud.write_text('export default {"name":"acloud"};\n')
            original = acloud.read_bytes()
            self.assertTrue(sync.refresh(sdk, app, destination))
            self.assertFalse(sync.refresh(sdk, app, destination))
            changed = copy.deepcopy(TREE)
            changed["commands"]["run"]["commands"]["jobs"] = {"commands": {}, "flags": {}}
            make_sdk(sdk, changed, "591.0.0")
            self.assertTrue(sync.refresh(sdk, app, destination))
            self.assertEqual(acloud.read_bytes(), original)
            for name in ("git", "pnpm"):
                self.assertEqual((destination / "specs" / (name + ".js")).resolve(), (bundle / (name + ".js")).resolve())
            self.assertIn('"acloud"', (destination / "specs/index.js").read_text())
            current = (destination / "gcloud-current").resolve()
            make_sdk(sdk, {"invalid": True}, "592.0.0")
            with self.assertRaises(ValueError):
                sync.refresh(sdk, app, destination)
            self.assertEqual((destination / "gcloud-current").resolve(), current)
            self.assertEqual(acloud.read_bytes(), original)

    def test_hook_checks_only_once_until_sdk_version_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sdk = make_sdk(root / "sdk with spaces")
            repo = root / "repo"
            (repo / "scripts").mkdir(parents=True)
            destination = root / "data"
            destination.mkdir()
            count = root / "count"
            (repo / "scripts/sync.py").write_text("from pathlib import Path\nwith Path(" + repr(str(count)) + ").open('a') as f: f.write('sync\\n')\n")
            with patch.object(sync, "ROOT", repo):
                hook = sync.hook_text(root / "Figxit.app", destination, sdk, sys.executable)
            script = root / "hook.sh"
            script.write_text(hook)
            cmd = '. "$1"; _figxit_gcloud_refresh; printf "591.0.0\\n" > "$2/VERSION"; _figxit_gcloud_refresh; _figxit_gcloud_refresh'
            env = dict(os.environ, PATH=str(sdk / "bin") + os.pathsep + os.environ["PATH"])
            subprocess.run(["bash", "--noprofile", "--norc", "-c", cmd, "test", str(script), str(sdk)], env=env, check=True)
            self.assertEqual(count.read_text().splitlines(), ["sync", "sync"])
