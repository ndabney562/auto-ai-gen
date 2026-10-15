"""Trusted publisher: never imports candidate modules or runs candidate commands."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class GitHubPublisher:
    def __init__(self, repository: str):
        parts = repository.split("/")
        if len(parts) != 2 or not all(
                part and all(c.isalnum() or c in "-_." for c in part) for part in parts):
            raise ValueError("Repository must be owner/name")
        self.repository = repository

    def _gh(self, *args):
        return subprocess.run(["gh", *args], check=True, capture_output=True,
                              text=True, timeout=90).stdout

    def publish(self, workspace: Path, task_id: str, sha: str) -> dict:
        if not task_id.isalnum() or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid task or candidate SHA")
        branch = "agent/subscription-" + task_id + "-" + sha[:12]
        remote = f"https://github.com/{self.repository}.git"
        # gh supplies credentials only to trusted git transport, never candidate code.
        config = ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=",
                  "-c", "credential.helper=!gh auth git-credential", "-C", str(workspace)]
        result = subprocess.run([*config, "ls-remote", remote, f"refs/heads/{branch}"],
                                check=True, capture_output=True, text=True, timeout=90)
        existing = result.stdout.split()
        if existing and existing[0] != sha:
            raise ValueError("Remote candidate branch changed; refusing overwrite")
        if not existing:
            subprocess.run([*config, "push", f"--force-with-lease=refs/heads/{branch}:", remote,
                            f"{sha}:refs/heads/{branch}"], check=True, capture_output=True, timeout=90)
        pulls = json.loads(self._gh("pr", "list", "--repo", self.repository, "--head", branch,
                                   "--state", "all", "--json", "number,url,headRefOid,state"))
        if pulls:
            if len(pulls) != 1 or pulls[0]["headRefOid"] != sha or pulls[0]["state"] == "CLOSED":
                raise ValueError("Existing PR does not match this candidate")
            return pulls[0]
        self._gh("pr", "create", "--repo", self.repository, "--base", "main", "--head", branch,
                 "--title", "Subscription agent: " + task_id,
                 "--body", "Candidate passed isolated validation and independent model review. "
                 "The local durable task record binds both to the exact candidate commit.")
        pulls = json.loads(self._gh("pr", "list", "--repo", self.repository, "--head", branch,
                                   "--json", "number,url,headRefOid,state"))
        if len(pulls) != 1 or pulls[0]["headRefOid"] != sha:
            raise ValueError("Could not reconcile published candidate PR")
        return pulls[0]

    def verified_merge(self, number: int, sha: str, base_sha: str) -> bool:
        """Require exact-head CI, current main, and unchanged PR before SHA-guarded merge."""
        prefix = f"repos/{self.repository}"
        pr = json.loads(self._gh("api", f"{prefix}/pulls/{number}"))
        if (pr["head"]["sha"] != sha or pr["head"]["repo"]["full_name"] != self.repository
                or pr["base"]["ref"] != "main" or pr["draft"]):
            raise ValueError("PR provenance or candidate changed")
        if pr.get("merged"):
            return True
        if pr["state"] != "open":
            raise ValueError("Candidate PR is closed")
        main = json.loads(self._gh("api", f"{prefix}/git/ref/heads/main"))
        if main["object"]["sha"] != base_sha:
            raise ValueError("Main advanced; rebase and revalidate before merging")
        runs = json.loads(self._gh("api", f"{prefix}/actions/runs?head_sha={sha}&per_page=100"))
        eligible = [run for run in runs["workflow_runs"] if run["name"] == "CI"
                    and run["head_sha"] == sha and run["event"] == "pull_request"
                    and run["path"] == ".github/workflows/ci.yml"]
        if not eligible:
            return False
        latest = max(eligible, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
        if latest["status"] != "completed" or latest["conclusion"] != "success":
            return False
        jobs = json.loads(self._gh("api", f"{prefix}/actions/runs/{latest['id']}/jobs?per_page=100"))
        verify = [j for j in jobs["jobs"] if j["name"] == "verify" and j["conclusion"] == "success"]
        if len(verify) != 1 or not all(any(s["name"] == name and s["conclusion"] == "success"
                                         for s in verify[0]["steps"]) for name in ("Lint", "Test")):
            return False
        if pr.get("mergeable") is not True:
            return False
        # GitHub must close the race between our main-SHA check and the merge.
        policy = json.loads(self._gh("api", f"{prefix}/branches/main/protection"))
        checks = policy.get("required_status_checks") or {}
        contexts = set(checks.get("contexts", [])) | {
            check["context"] for check in checks.get("checks", [])}
        if (checks.get("strict") is not True or "verify" not in contexts
                or policy.get("enforce_admins", {}).get("enabled") is not True):
            raise ValueError("Automatic merge requires strict verify checks enforced for admins")
        merged = json.loads(self._gh("api", "--method", "PUT", f"{prefix}/pulls/{number}/merge",
                                    "-f", f"sha={sha}", "-f", "merge_method=merge"))
        return merged.get("merged") is True


    def ci_failure(self, sha: str) -> str | None:
        payload = json.loads(self._gh("api", f"repos/{self.repository}/actions/runs?head_sha={sha}&per_page=100"))
        runs = [run for run in payload["workflow_runs"] if run["name"] == "CI"
                and run["head_sha"] == sha and run["event"] == "pull_request"]
        if not runs:
            return None
        latest = max(runs, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
        if latest["status"] == "completed" and latest["conclusion"] in {
                "failure", "timed_out", "cancelled"}:
            log = self._gh("run", "view", str(latest["id"]), "--repo", self.repository, "--log-failed")
            return log[-16000:] or "GitHub CI did not pass"
        return None
