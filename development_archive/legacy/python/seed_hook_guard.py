"""Restore this installation when its existing capture service stops."""
import json
from pathlib import Path
import time

from seed_export_hook import ROOT, OUT, restore


def main():
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    installation = manifest['installation_id']
    lease_path = ROOT / 'output/auto_capture/service_lease.json'
    lease = json.loads(lease_path.read_text(encoding='utf-8'))
    instance = lease['instance']
    deadline = time.monotonic()+24*3600
    while time.monotonic() < deadline:
        try:
            manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
            if not manifest.get('installed') or manifest.get('installation_id') != installation:
                return
            lease = json.loads(lease_path.read_text(encoding='utf-8'))
            if not lease.get('running') or lease.get('instance') != instance:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    restore(expected_installation_id=installation)


if __name__ == '__main__':
    main()
