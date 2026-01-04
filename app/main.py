from app.core.pipeline import run_pipeline


def main():
    res = run_pipeline("app/data/sample_payload.json", out_dir="app/reports")
    print("PIPELINE OK")
    print("Segments:", res["meta"]["segments"])
    print("Last end:", res["meta"]["last_end"])
    print("Saved:", res["files"]["gantt_html"])
    print("Saved:", res["files"]["dashboard_html"])
    print("Saved: app/reports/result.json")


if __name__ == "__main__":
    main()
