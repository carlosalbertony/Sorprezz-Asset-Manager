"""One packaging command shared by local builds and GitHub Actions."""
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
        '--name', 'SorprezzAssetManager', '--icon', 'assets/sorprezz.ico',
        '--add-data', 'web;web', '--collect-all', 'webview',
        '--collect-all', 'googleapiclient',
        '--collect-submodules', 'google_auth_oauthlib',
        '--collect-submodules', 'google.auth', '--collect-submodules', 'google.oauth2',
        'desktop.py',
    ], cwd=Path(__file__).resolve().parent, check=True)
