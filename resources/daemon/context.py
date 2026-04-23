"""Per-thread/task logging context for the SMA-NG daemon.

Provides a ``_job_id`` context variable that workers set at the start of each
job. A ``JobContextFilter`` attached to the DAEMON log handler reads this value
and injects it into every ``LogRecord`` as ``job_id``, making log lines
automatically correlated with the running job without changing call sites.

Usage (in format strings)::

    %(job_id)s
"""

import contextvars
import logging

# Current job ID for the executing thread/task.  Workers set this at job start
# and reset it on completion.  The default "-" signals "no active job".
_job_id: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")


def set_job_id(job_id: int | str | None) -> contextvars.Token:
  """Set the job ID for the current thread context.

  Returns the token produced by ContextVar.set() so the caller can restore
  the previous value via reset() if needed.
  """
  return _job_id.set(str(job_id) if job_id is not None else "-")


def clear_job_id(token: contextvars.Token) -> None:
  """Restore the job ID context to its state before the matching set_job_id call."""
  _job_id.reset(token)


class JobContextFilter(logging.Filter):
  """Injects the current job ID into every LogRecord as ``job_id``.

  Attach this filter to the DAEMON handler (or the DAEMON logger itself) so
  that ``%(job_id)s`` can be used in format strings.  Records that have no
  active job receive ``job_id="-"``.
  """

  def filter(self, record: logging.LogRecord) -> bool:
    record.job_id = _job_id.get()  # type: ignore[attr-defined]
    return True
