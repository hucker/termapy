"""variables-isolated: a test cannot see the ``$(NAME)`` state an earlier test left behind.

The two tests below share an xdist group, so they run on one worker in this
order (and in file order under ``-n 0``): the first writes to every store in
``termapy.variables``, the second checks that none of it survived.  Run alone,
the second passes trivially; it is the pair that proves the fixture.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from termapy import variables

_LEAK = "ISOLATION_PROBE"


@pytest.mark.xdist_group("variables-isolation")
class TestVariablesDoNotLeak:
    def test_a_test_writes_to_every_store(self):
        """Set a value in each of the four stores, as a frontend and a script would."""
        # Act
        variables._VARS[_LEAK] = "user"
        variables.set_launch_var(_LEAK, "launch")
        variables.set_context_var(_LEAK, lambda: "context")
        variables._FROZEN_MOMENTS[_LEAK] = datetime(2000, 1, 1)

        # Assert
        assert variables.expand_vars(f"$({_LEAK})") == "user", (
            "the write landed where expansion reads it"
        )

    def test_the_next_test_sees_none_of_it(self):
        """The fixture restored every store, so nothing the previous test wrote is left."""
        # Act
        leaked = [
            name
            for name, store in (
                ("_VARS", variables._VARS),
                ("_LAUNCH_VARS", variables._LAUNCH_VARS),
                ("_CONTEXT_VARS", variables._CONTEXT_VARS),
                ("_FROZEN_MOMENTS", variables._FROZEN_MOMENTS),
            )
            if _LEAK in store
        ]

        # Assert
        assert leaked == [], f"state crossed from one test to the next: {leaked}"
        assert variables.expand_vars(f"$({_LEAK})") == f"$({_LEAK})", "the name is unknown again"
