"""Tasks' data access and orchestration — spec 008 plan §5.2.

`store` is the only module that reads the task tables for the task service
or calls the task database functions. Services gather rows, ask
`app.domain.tasks` to decide, and write through `task_apply`, so a change
and its audit row land together (FR-TASK-DATA-10).
"""
