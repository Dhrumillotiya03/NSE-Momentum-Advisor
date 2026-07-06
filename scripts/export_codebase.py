import os

PROJECT_ROOT = ".."
OUTPUT_FILE = "../complete_codebase.txt"

# folders we want to include
INCLUDE_DIRS = [
    ".",        # root folder
    "scripts",
    "data"
]

# file types to include
INCLUDE_EXT = [".py", ".json", ".yaml", ".txt"]

def should_include(file):
    return any(file.endswith(ext) for ext in INCLUDE_EXT)


def collect_files():

    files = []

    for folder in INCLUDE_DIRS:

        path = os.path.join(PROJECT_ROOT, folder)

        for root, dirs, filenames in os.walk(path):

            for f in filenames:

                if should_include(f):
                    files.append(os.path.join(root, f))

    return sorted(files)


def write_output(files):

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:

        out.write("=====================================\n")
        out.write("FULL PROJECT CODEBASE EXPORT\n")
        out.write("=====================================\n\n")

        for f in files:

            out.write("\n\n")
            out.write("=====================================\n")
            out.write(f"FILE: {f}\n")
            out.write("=====================================\n\n")

            try:
                with open(f, "r", encoding="utf-8") as src:
                    out.write(src.read())

            except Exception as e:
                out.write(f"[ERROR READING FILE: {e}]\n")


def main():

    print("Collecting project files...")

    files = collect_files()

    print(f"{len(files)} files found.")

    write_output(files)

    print(f"\nComplete codebase written to:\n{OUTPUT_FILE}")


if __name__ == "__main__":
    main()