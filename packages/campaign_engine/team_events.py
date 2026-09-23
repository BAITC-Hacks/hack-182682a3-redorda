"""Evidence-linked events for real runner steps, independent of UI animation."""

from uuid import uuid4


class TeamJournal:
    def __init__(self, emit):
        self.emit = emit
        self.prefix = uuid4().hex
        self.active = None
        self.previous = None

    def begin(self, task_id, actor_id, title):
        evidence = self.previous['artifact_ids'] if self.previous else []
        task = {'task_id': task_id, 'actor_id': actor_id, 'title': title,
                'artifact_ids': [], 'evidence_ids': evidence}
        if self.previous:
            self.emit('task_handoff', **self.previous,
                      from_actor=self.previous['actor_id'], to_actor=actor_id,
                      to_task_id=task_id, to_title=title)
        self.active = task
        self.emit('task_started', **task, status='running')

    def complete(self, kind, data):
        task = self.active
        artifact_id = f"{self.prefix}:{task['task_id']}"
        artifact = {'id': artifact_id, 'task_id': task['task_id'], 'type': kind,
                    'title': task['title'], 'data': data,
                    'evidence_ids': task['evidence_ids']}
        completed = {**task, 'artifact_ids': [artifact_id]}
        self.emit('task_completed', **completed, status='completed', artifacts=[artifact])
        self.previous = {**completed, 'evidence_ids': [artifact_id]}
        self.active = None

    def fail(self, reason):
        if self.active:
            self.emit('task_failed', **self.active, status='failed', reason=reason)
            self.active = None
