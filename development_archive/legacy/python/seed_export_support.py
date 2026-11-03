"""Read only the current game's explicitly exported seed, after opt-in setup."""
import json
from pathlib import Path
import threading
import time

from inspect_game_package import APPID, PACKAGES
from map_protocol import valid_seed

EXPORT = PACKAGES.parents[2] / 'local' / APPID / 'usr/sheepsolver_seed.json'


def read_seed_export(path, request_seed, not_before):
    if not path.exists() or path.stat().st_size > 4096:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (value.get('capture_format') != 1 or value.get('request_seed') != request_seed
                or not valid_seed(value.get('map_seed'))
                or not isinstance(value.get('exported_at'), (int, float))
                or value['exported_at'] < not_before*1000):
            return None
        return {k: value[k] for k in ('map_seed', 'map_seed_2') if k in value}
    except (OSError, ValueError, TypeError):
        return None


def enable_seed_exports(capture):
    original_start = capture.accept_start
    stopped = threading.Event()
    original_done = capture.done

    def poll(session, request_seed, not_before):
        # A thread per active game, bounded to three minutes, checking only a
        # single game-specific seed file. A new game immediately ends old work.
        deadline = time.monotonic()+180
        while not stopped.wait(0.5) and time.monotonic() < deadline:
            with capture.lock:
                if not capture.current or capture.current['id'] != session:
                    return
                if valid_seed(capture.current['seed']):
                    return
            value = read_seed_export(EXPORT, request_seed, not_before)
            if value:
                with capture.lock:
                    if capture.current and capture.current['id'] == session:
                        capture.set_seed(value)
                        capture.status(seed_source='game_local_export')
                return

    def start(data, *args, **kwargs):
        original_start(data, *args, **kwargs)
        with capture.lock:
            if capture.current and data.get('need_seed'):
                session = capture.current['id']
                capture.status(seed_export_enabled=True, seed_export_path=str(EXPORT))
                # Permit the callback's export just before the HTTP response
                # handler completes, while still rejecting prior-game records.
                threading.Thread(target=poll, args=(session, data.get('map_seed_2'), time.time()-5),
                                 daemon=True).start()

    def done():
        stopped.set()
        original_done()

    capture.accept_start = start
    capture.done = done
    return capture
