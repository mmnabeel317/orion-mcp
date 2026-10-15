"""Test helpers for faking Orion runs.

Tool functions call ``run_orion`` (which shells out to the Orion CLI and hits
Elasticsearch). Tests replace it with ``FakeOrion`` so tools can be exercised
without any external service.
"""

import json
import subprocess


def make_result(stdout="", returncode=0, stderr="") -> subprocess.CompletedProcess:
    """Build a CompletedProcess like the one run_orion returns.

    Non-string ``stdout`` is JSON-encoded, mirroring Orion's ``-o json`` output.
    """
    if not isinstance(stdout, str):
        stdout = json.dumps(stdout)
    return subprocess.CompletedProcess(args=["orion"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeOrion:  # pylint: disable=too-few-public-methods
    """Async stand-in for run_orion that records calls and replays canned results.

    ``results`` may be a single CompletedProcess (returned for every call) or a
    dict mapping config path -> CompletedProcess.
    """

    def __init__(self, results):
        self.results = results
        self.calls: list[dict] = []

    async def __call__(self, config, version, lookback, **kwargs):
        self.calls.append({"config": config, "version": version, "lookback": lookback, **kwargs})
        if isinstance(self.results, dict):
            return self.results[config]
        return self.results
