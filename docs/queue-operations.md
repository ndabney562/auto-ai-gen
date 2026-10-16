# Durable queue operations

The installed LaunchAgent starts the queue service at login. Do not start a duplicate detached service. Stop the service and unload the LaunchAgent before changing global configuration; reload it afterward. Login startup does not establish full reboot acceptance.

Create a task, then configure the queue while the service is stopped and unloaded:

```sh
python -m orchestrator.subscription_runner --state STATE create --repo REPO --ref origin/main --objective TEXT --target docs/example.md
python -m orchestrator.supervisor --state STATE configure-queue --repository owner/repo --image sha256:IMAGE_ID --merge-verified
```

Replace STATE, REPO, TEXT, IMAGE_ID, and task IDs with your values. Quote values containing spaces. Global `--state` always precedes the subcommand.

With the service running, enroll tasks and inspect or control the queue:

```sh
python -m orchestrator.supervisor --state STATE enqueue TASK_ID
python -m orchestrator.supervisor --state STATE enqueue SECOND --after FIRST
python -m orchestrator.supervisor --state STATE status
python -m orchestrator.supervisor --state STATE cancel --task TASK_ID
python -m orchestrator.supervisor --state STATE stop
```

Enrollment is supported while the service runs. Use `--after FIRST` to declare an explicit dependency: SECOND is released only after FIRST is confirmed DONE. Submission order alone does not establish a dependency.

Retries are isolated per task, so one task's retry state does not consume another task's retry allowance. Quota waits resume automatically when capacity becomes available; they do not trigger self-review or an API-credit fallback.

Use `status` to inspect progress, `cancel --task TASK_ID` to cancel a task, and `stop` to stop the service. Before changing global configuration, also unload the installed LaunchAgent using the procedure in [Background supervisor](background-supervisor.md).

See [Durable queue](durable-queue.md) for queue behavior and [Background supervisor](background-supervisor.md) for service lifecycle details.
