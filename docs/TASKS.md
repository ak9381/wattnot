# TASKS: ordered build list (Oct 27 – Nov 4, IST)

One task is about 1–3 hours of work. For each task: read its module PRD and TDD, write tests first, make them pass, push the branch. Tasks marked **(manual)** involve accounts, money or public actions and need a maintainer.


Working rules
- One branch per task: `feat/<module>-<slug>`, touching only the paths its module TDD lists.
- Shared files (`schemas/`, `Makefile`, `pyproject.toml`, `web/package.json`) change only in SHARED tasks, merged before dependants start.
- Heavy jobs (GPU sweep, model download, full gate run) run one at a time. Take `/tmp/wattnot-heavy.lock` first.
