import subprocess
import sys


def test_cli_requires_explicit_mode():
    result = subprocess.run([sys.executable, '-m', 'app.run_pipeline', '--output', 'unused'], capture_output=True, text=True)
    assert result.returncode != 0
    assert '--synthetic' in result.stderr


def test_synthetic_pipeline_records_and_uses_selected_config(tmp_path):
    import json
    from app.run_pipeline import BEST_CONFIG
    result = subprocess.run([sys.executable, '-m', 'app.run_pipeline', '--synthetic', '--output', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    metrics = json.loads((tmp_path / 'evaluation' / 'metrics.json').read_text())
    assert manifest['recommender_config'] == BEST_CONFIG
    assert metrics['protocol']['recommender_config'] == BEST_CONFIG
