from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception as e:
    raise RuntimeError(
        "Tkinter bulunamadı. Windows Python kurulumunda genelde hazır gelir. "
        "Eğer yoksa 'python' yerine farklı interpreter kullanıyor olabilirsin."
    ) from e


APP_ROOT = Path(__file__).resolve().parent
OUT_DIR = APP_ROOT / "out"
GANTT_HTML = OUT_DIR / "gantt.html"


def run_simulation(excel_path: str) -> tuple[int, str]:
    """
    Runs: python -m app.main --excel "<excel_path>"
    Returns (return_code, combined_output).
    """
    cmd = [sys.executable, "-m", "app.main", "--excel", excel_path]
    p = subprocess.run(
        cmd,
        cwd=str(APP_ROOT.parent),  # project root (folder containing "app")
        capture_output=True,
        text=True,
    )
    output = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, output


def pick_excel() -> str | None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    file_path = filedialog.askopenfilename(
        title="ProductionSim - Excel seç",
        filetypes=[("Excel files", "*.xlsx")],
    )
    return file_path or None


def main():
    excel_path = pick_excel()
    if not excel_path:
        return

    code, out = run_simulation(excel_path)

    if code != 0:
        # Show last part to keep dialog readable
        tail = out[-3000:] if len(out) > 3000 else out
        messagebox.showerror("Simülasyon Hatası", tail)
        return

    if not GANTT_HTML.exists():
        messagebox.showwarning(
            "Tamamlandı ama Gantt yok",
            f"Sim tamamlandı ama gantt.html bulunamadı:\n{GANTT_HTML}",
        )
        return

    webbrowser.open(GANTT_HTML.as_uri())
    messagebox.showinfo("Tamam", f"Sim tamamlandı.\nGantt açıldı:\n{GANTT_HTML}")


if __name__ == "__main__":
    main()