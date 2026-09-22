import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help", "--version"):
        from .cli import main as cli_main
        return cli_main(argv)
    if argv:
        from .cli import COMMANDS
        if argv[0] in COMMANDS:
            from .cli import main as cli_main
            return cli_main(argv)
        if argv[0] in ("--unpack", "--pack"):
            # вызов из контекстного меню файлового менеджера: окно с прогрессом
            from .app import run_gui
            return run_gui(sys.argv[:1], task=(argv[0][2:], argv[1:]))
    from .app import run_gui
    return run_gui(sys.argv[:1] + argv)


if __name__ == "__main__":
    sys.exit(main())
