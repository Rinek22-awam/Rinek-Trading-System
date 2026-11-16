"""Run the terminal diagnostics with redirected input and output for Tkinter."""
import sys

from copier import envfile
from copier.config import load_config
from . import check, main


def run(task):
    envfile.load()
    if task == "reset-bybit":
        main.reset_bybit_journal()
        return
    cfg = load_config("config.yaml")
    if task == "readiness":
        check.run_all(cfg)
    elif task == "history":
        main.history(cfg)
    elif task == "reset-mt5":
        main.reset_journal(cfg)


if __name__ == "__main__":
    run(sys.argv[1])
