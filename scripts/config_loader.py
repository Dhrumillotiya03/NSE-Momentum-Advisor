import yaml

CONFIG_PATH = "../config.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    print(load_config())