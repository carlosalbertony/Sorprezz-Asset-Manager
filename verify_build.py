"""Run the installed-runtime check without touching the user's configuration."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


if __name__ == '__main__':
    executable = Path(__file__).resolve().parent / 'dist/SorprezzAssetManager/SorprezzAssetManager.exe'
    with tempfile.TemporaryDirectory(prefix='sorprezz-build-check-') as temp:
        report = Path(temp) / 'report.json'
        result = subprocess.run([str(executable), '--check-installation', str(report)],
                                env={**os.environ, 'LOCALAPPDATA': temp}, timeout=90)
        if report.exists():
            print(report.read_text(encoding='utf-8'))
        result.check_returncode()
        if not report.exists() or not json.loads(report.read_text(encoding='utf-8')).get('ok'):
            raise RuntimeError('La aplicación compilada no completó la comprobación')
