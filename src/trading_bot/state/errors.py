"""State-layer exceptions."""

from __future__ import annotations


class StateError(Exception):
    """Base class for state/persistence failures."""


class StateAlreadyExistsError(StateError):
    """A daily-state row already exists (conditional create lost the race)."""


class ConcurrentTransitionError(StateError):
    """A guarded status transition failed because the row wasn't in the expected state.

    This is the conditional-write gate firing (DECISIONS.md §4): another
    overlapping/retried run already moved the state. The safe response is to do
    nothing — never force the transition.
    """
