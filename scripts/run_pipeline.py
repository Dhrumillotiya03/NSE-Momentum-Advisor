import subprocess


def run(script):
    subprocess.run(["python", script])


def main():

    print("\n==============================")
    print("⚙️ DAILY PIPELINE RUN")
    print("==============================")

    print("\nUpdating signals...")
    run("stock_alpha_v2.py")

    print("\nGenerating decision report...")
    run("daily_decision.py")

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()