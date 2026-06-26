"""AWS deployment surface (DECISIONS.md §3, §13 step 7).

The Lambda entrypoint and the SSM SecureString secrets loader. Everything here is
thin glue: it loads secrets into the environment, then builds and runs the *same*
engine the local runner uses. boto3 is the only extra dependency and is imported
lazily, so importing the rest of the package never requires the ``aws`` extra.
"""

from __future__ import annotations
