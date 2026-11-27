import subprocess
import sys


def test_cli_requires_explicit_mode():
    result = subprocess.run([sys.executable, '-m', 'app.run_pipeline', '--output', 'unused'], capture_output=True, text=True)
    assert result.returncode != 0
    assert '--synthetic' in result.stderr
