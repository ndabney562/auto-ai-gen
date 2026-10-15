# Reviewed patch security boundaries

The legacy API engineering workflow is still opt-in and requires API keys; it
is not the subscription runner. Generated Python is validated on a fresh job
with read-only repository permission, no provider secrets, and no persisted
checkout credential. The publishing job never imports or runs candidate code.
Both jobs use the same base commit and immutable reviewed-patch artifact.
Bot proposals use `agent/` branches so the legacy stage auto-merge workflow does
not treat them as eligible for automatic merge.

Patch paths reject symbolic links (including internal aliases) and hardlinks.
The coordinator checks source contents again after review to preserve intervening
edits. Patch application holds a workspace lock and writes an fsynced undo journal
before replacing files. Exceptions restore original contents; the next engineering
invocation recovers an interrupted application before reading sources.

Multi-file filesystem replacement is recoverable, not simultaneously visible:
uncoordinated readers can observe intermediate files. Use a private checkout and
publish a Git commit only after successful application and validation. Do not run
another untrusted process against that checkout during review/application. This
path validation is not an operating-system sandbox against a racing hostile process.
