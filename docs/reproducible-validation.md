# Reproducible validation

Merged main `95126041ce068984cd1b61018a716dfe81a0502b` passed all 94 tests and
lint in the restricted ARM64 validator, and GitHub CI passed on October 16, 2026.
Main requires pull requests and strict, up-to-date `verify` checks enforced for
administrators. Review and merge are delegated to the coding agent; an additional
human GitHub approval is not required. Force pushes and deletion are disabled.

CI and the validator install the same exact Python versions from
`validation/requirements.lock`, requiring package hashes and binary wheels.
The validator additionally pins its multi-platform Python base digest and Debian
package snapshot. Build from a trusted checkout, never from candidate code:

```sh
docker build -f validation/Dockerfile -t auto-ai-gen-validator:locked .
docker image inspect auto-ai-gen-validator:locked --format '{{.Id}}'
```

Use the resulting `sha256:...` image ID for unattended runs, not a mutable tag.
Keep that image locally; the validator uses `--pull=never`. The image ID identifies
one architecture's complete built environment. ARM64 and AMD64 IDs differ.

To update dependencies, edit exact versions in `validation/requirements.in`, then
use Python 3.12 and pip-tools 7.5.3:

```sh
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
  --output-file validation/requirements.lock validation/requirements.in
```

Review the lock and image changes, rebuild, and validate on both local ARM64 and
GitHub AMD64 before merging. Dependency updates are deliberate maintenance, not
part of model-generated tasks. Project metadata retains supported version ranges;
the validation lock records the tested environment. No API credentials are needed.
