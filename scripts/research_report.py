import subprocess


def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


def main():

    print("\n==============================")
    print("📄 MARKET RESEARCH REPORT")
    print("==============================")

    print("\n--- MARKET ENVIRONMENT ---")
    print(run("adaptive_engine.py"))

    print("\n--- SECTOR ROTATION ---")
    print(run("sector_rotation.py"))

    print("\n--- TOP OPPORTUNITIES ---")
    print(run("portfolio_engine.py"))

    print("\n--- EXIT WARNINGS ---")
    print(run("exit_engine.py"))

    print("\n--- PORTFOLIO STATUS ---")
    print(run("portfolio_state.py"))

    print("\n==============================")
    print("🧠 SUMMARY")
    print("==============================")
    print("Review sector leaders and align new investments accordingly.")
    print("Avoid weak sectors unless strong stock-specific reasons exist.")


if __name__ == "__main__":
    main()