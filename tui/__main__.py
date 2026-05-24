import sys

def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'setup':
        from tui.setup_wizard import run_setup
        run_setup()
    else:
        from tui.app import ForwarderApp
        ForwarderApp().run()

if __name__ == '__main__':
    main()
