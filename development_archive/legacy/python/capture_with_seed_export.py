"""Opt-in capture loader paired with the reversible local game export hook."""
from auto_capture import AutoCapture
from seed_export_support import enable_seed_exports

addons = [enable_seed_exports(AutoCapture())]
