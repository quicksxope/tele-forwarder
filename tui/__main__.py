import argparse as _argparse
import os as _os
import sys

# Parse --data-dir before importing anything that touches paths.
_p = _argparse.ArgumentParser(add_help=False)
_p.add_argument('--data-dir', metavar='DIR')
_early, _ = _p.parse_known_args()
if _early.data_dir:
    _os.environ['TELE_FORWARDER_DATA_DIR'] = _early.data_dir


def main():
    if 'setup' in sys.argv[1:]:
        from tui.setup_wizard import run_setup
        run_setup()
    else:
        from tui.app import ForwarderApp
        ForwarderApp().run()

if __name__ == '__main__':
    main()
