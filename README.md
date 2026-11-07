# gcloud completion for Figxit

Generate Fig completion specs directly from the official completion data included in the installed Google Cloud CLI. No hand-maintained command list and no Google Cloud authentication are required.

The checked-in snapshot was generated from SDK **590.0.1**: **21,444 command/group nodes**, **17,472 executable commands**, and **99,941 flag definitions**, across GA, beta and alpha. Product groups are loaded lazily through 405 Fig spec modules.

## Install on macOS

Requires Figxit (tested with 0.5.0), gcloud, and Python 3.9 or later. Keep this checkout at its installation path; the shell hook calls its generator.

```sh
git clone https://github.com/ringomax/gcloud-completion.git
cd gcloud-completion
python3 scripts/sync.py --install
```

Open a new terminal tab after installation. Try:

```text
gcloud run deploy SERVICE --scaling
gcloud beta run worker-pools
gcloud alpha
```

The installer retains Figxit's bundled git and pnpm definitions, including pnpm's package scripts. It also preserves other custom specs such as acloud. It adds gcloud to a shared overlay under `~/Library/Application Support/figxit-custom`, without changing the signed Figxit application.

Existing gcloud definitions are backed up locally. Generated releases are written into a new directory and switched through a single symlink after conversion succeeds. A failed conversion leaves the previous generation active.

The installer adds a small source line before Figxit initialization in `~/.zshrc` and `~/.profile`, with private backups of these files. The zsh hook checks the SDK version at each prompt, without launching Python when the version is unchanged. After a gcloud update or version switch, it generates a matching definition and restarts Figxit to clear its module cache. Bash refreshes at shell startup. Direct SDK paths, SDK symlinks, and mise shims are supported.

A macOS LaunchAgent opens Figxit with the overlay at login. Use this launcher instead of the app's built-in Launch at Login setting to avoid an instance starting without the custom environment. SDK changes made during a session are applied at the next zsh prompt; otherwise they are applied when a new shell starts. Diagnostics are kept in `~/Library/Application Support/figxit-custom/gcloud-sync.log`.

Other locations can be selected with `--app`, `--destination`, and `--sdk-root`. Run `python3 scripts/sync.py` for a manual refresh.

## How generation works

The source is `data/cli/gcloud_completions.py`, distributed by Google with the SDK. It contains commands, flags, Boolean markers, and enumerated values. The generator parses this file as literal data without executing it or importing SDK modules. Every source command and flag is preserved, and a validator checks the entire generated tree against its source.

This follows the SDK installed on your machine. It does not upgrade gcloud or add commands from a release you have not installed. The installed SDK's completion data may describe optional components that are not installed.

The compact SDK tree does not contain per-command help text or positional argument schemas. Resource names use generic positional placeholders; live account resources are not queried. Options marked dynamic by Google accept a value, but do not fetch suggestions from cloud resources. If Google changes the tree format, generation fails rather than silently publishing partial definitions.

## GitHub Actions monitoring

The `Latest SDK` workflow runs daily at **00:23 UTC** and can be started manually. It installs the latest official SDK, generates definitions, validates all source command/flag paths, runs the unit tests, and uploads a versioned artifact. Its run summary reports whether the SDK version and source-data hash differ from the checked-in snapshot.

The schedule runs after this workflow is merged into the repository's default branch. Generation failure fails the workflow. This checks compatibility with Google's releases; local version tracking works independently and does not wait for a repository update or a merged PR. The checked-in `specs/` directory is a reproducible snapshot, while the scheduled artifacts reflect the latest tested SDK.

To regenerate the checked-in snapshot:

```sh
python3 scripts/generate.py
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

Generated metadata records the exact SDK version and source-data SHA-256. No credentials, cloud project IDs or local SDK paths are included in generated files.

## Remove automatic gcloud refresh

Remove the `gcloud-completion automatic version sync` source line and its comment from `~/.zshrc` and `~/.profile`, then start a new shell. Other Figxit shell initialization lines should remain.

The generated definitions remain usable. To restore an earlier gcloud definition, select the retained generation or the `legacy-gcloud-*` backup under the shared configuration directory. To remove the shared Figxit overlay entirely, first account for other custom specs such as acloud, then remove the `local.figxit.custom-specs` LaunchAgent, unset `FIGXIT_SPECS` with `launchctl`, and restart Figxit without that variable.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the Google Cloud CLI completion-data attribution.
