"""Tasks — spec 008. Pure, like the rest of `app.domain`, except `service`.

Each module decides one area and reads nothing: the facts come in as
arguments (the viewer, the task, `today`, `now`, the zone), and the answer is
a value, None (allowed) or a refusal sentence safe to show the person.
`service.py` is the one exception (plan §4 CT-2): it is the task service, the
008/009 interface `create_task`, and reads and writes through
`app.services.tasks.store`.
"""
