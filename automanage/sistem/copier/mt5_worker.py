"""One process owns each connection to the native MT5 Python module."""
import multiprocessing
import os
from types import SimpleNamespace

from .broker import Broker


def _serve(pipe, cfg, path, identity, credentials):
    broker = None
    try:
        broker = Broker(cfg)
        if not broker.connect(*credentials, path=path, timeout=5000):
            raise ConnectionError("Terminal MT5 gagal terhubung")
        pipe.send((True, None))
        while True:
            method, args = pipe.recv()
            try:
                account = broker.account()
                terminal = broker.mt5.terminal_info()
                if account is None or terminal is None or not terminal.connected:
                    raise ConnectionError("Terminal MT5 belum login atau terputus")
                if path and os.path.normcase(os.path.realpath(terminal.path)) != os.path.normcase(
                        os.path.dirname(os.path.realpath(path))):
                    raise ConnectionError("Terminal yang tersambung berbeda dari direktori terdaftar")
                actual = [str(account.login), account.server]
                if identity and actual != identity:
                    raise ConnectionError("Login atau server terminal berbeda dari akun terdaftar")
                if method in {"place", "cancel", "modify_sl", "close"}:
                    if not identity:
                        raise ConnectionError("Identitas akun MT5 belum dicatat")
                if method in {"ready", "place", "cancel", "modify_sl", "close"}:
                    if not terminal.trade_allowed:
                        raise ConnectionError("Algo Trading belum aktif di terminal MT5")
                    if terminal.tradeapi_disabled:
                        raise ConnectionError("Trading lewat Python dinonaktifkan di pengaturan MT5")
                    if not account.trade_expert or not account.trade_allowed:
                        raise ConnectionError("Akun MT5 tidak mengizinkan trading otomatis")
                if method == "ready":
                    result = True
                elif method == "account":
                    result = SimpleNamespace(**account._asdict())
                elif method == "symbol_info":
                    info = broker.symbol_info(*args)
                    result = SimpleNamespace(**info._asdict()) if info else None
                elif method == "symbols":
                    result = [s.name for s in broker.mt5.symbols_get() or ()]
                elif method == "symbol_catalog":
                    result = [SimpleNamespace(name=s.name, visible=s.visible, trade_mode=s.trade_mode,
                                              description=s.description)
                              for s in broker.mt5.symbols_get() or ()]
                else:
                    result = getattr(broker, method)(*args)
                pipe.send((True, result))
            except Exception as error:
                pipe.send((False, str(error)))
    except EOFError:
        pass
    except Exception as error:
        pipe.send((False, str(error)))
    finally:
        if broker is not None:
            broker.mt5.shutdown()
        pipe.close()


class TerminalBroker:
    def __init__(self, cfg, path, identity=None, credentials=()):
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_serve, args=(child, cfg, path, identity, credentials), daemon=True)
        self.process.start()
        child.close()
        try:
            self._receive(7)
        except Exception:
            self.shutdown()
            raise

    def _receive(self, timeout):
        if not self.pipe.poll(timeout):
            self.shutdown()
            raise ConnectionError("Terminal MT5 tidak merespons")
        try:
            ok, result = self.pipe.recv()
        except EOFError as error:
            raise ConnectionError("Proses MT5 berhenti") from error
        if not ok:
            raise ConnectionError(result)
        return result

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args):
            self.pipe.send((name, args))
            return self._receive(5)

        return call

    def shutdown(self):
        self.pipe.close()
        self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=1)
