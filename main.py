"""Magyar Felirat Fordító - belépési pont.

Indítás:  python main.py
"""

from __future__ import annotations

import sys
import traceback


def main() -> int:
    from fordito.logsetup import setup_logging

    log_file = setup_logging()
    print(f"Debug napló: {log_file}")

    try:
        from fordito.gui import run
        run()
        return 0
    except Exception:
        import logging
        logging.getLogger("indulas").critical("Végzetes hiba:\n%s", traceback.format_exc())
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "Végzetes hiba",
                "A program nem tudott elindulni.\n\n"
                f"A részletek itt vannak: {log_file}\n\n{traceback.format_exc()[-600:]}",
            )
        except Exception:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
