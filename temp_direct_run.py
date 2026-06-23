import json

from pipeline.config import load_config
from pipeline.highlight import Pipeline


def main() -> None:
    cfg = load_config("settings.yaml")
    report = Pipeline(cfg).process_episode(
        r"C:\Users\User\Downloads\Связь (2013) 1080p.mp4",
        progress_callback=print,
    )
    print("\n===REPORT===")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
