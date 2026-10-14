"""The sales CRM's rules — spec 007. Pure, like the rest of `app.domain`.

Each module decides one area and reads nothing: the facts come in as
arguments, and the answer is None (allowed), a refusal sentence safe to show
the person, or a plan for a database function to write. The services gather
the rows and call these; the database functions write what was decided
(plan §5.2, §5.3).
"""
