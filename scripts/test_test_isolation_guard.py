from __future__ import annotations
"""Meta-tests for scripts/conftest.py docker and data-dir isolation."""


import subprocess

import pytest

from conftest import DockerMutationBlockedError


def test_docker_subprocess_guard_blocks_restart() -> None:
    with pytest.raises(DockerMutationBlockedError, match="docker mutations"):
        subprocess.run(["docker", "restart", "example-api"], capture_output=True, check=False)
