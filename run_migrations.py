#!/usr/bin/env python3
"""
Lance TOUTES les migrations v2 → v11 de façon robuste.

Deux styles coexistent dans le projet :
  - v2..v6      : logique dans un bloc `if __name__ == "__main__"`
  - v7..v9      : logique dans une fonction `run()` SANS bloc __main__
  - v10, v11    : les deux

`python -m module` n'exécute que le 1er style ; importer le module n'exécute
que le 2e. Ce script couvre les deux, dans l'ordre, de manière idempotente.
"""
import importlib
import runpy

for v in range(2, 12):
    name = f"shared.db.migration_v{v}"
    print(f">> {name}", flush=True)

    # Style A : exécuter le module comme `python -m` (déclenche __main__)
    try:
        runpy.run_module(name, run_name="__main__")
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"   (run_module: {exc})", flush=True)

    # Style B : appeler run() si présent (v7..v9, v10, v11)
    try:
        mod = importlib.import_module(name)
        fn = getattr(mod, "run", None)
        if callable(fn):
            fn()
    except Exception as exc:  # noqa: BLE001
        print(f"   (run(): {exc})", flush=True)

print(">> migrations terminées", flush=True)
