import argparse
import os

from diagnostics import run_diagnostics_summary, run_diagnostics_text
from gui import launch_gui, run_batch_via_gui_contract
from pipeline.config import load_config
from pipeline.highlight import Pipeline


def parse_args():
    parser = argparse.ArgumentParser(description="ShortsFactory CPU-first local short generator")
    parser.add_argument("--gui", action="store_true", help="Launch GUI")
    parser.add_argument("--batch", action="store_true", help="Run batch processing")
    parser.add_argument("--input-folder", type=str, help="Folder with source videos")
    parser.add_argument("--input-file", type=str, help="Single source video to process")
    parser.add_argument("--config", type=str, default="settings.yaml", help="Path to YAML config")
    parser.add_argument("--diagnostics", action="store_true", help="Run diagnostics and print the report")
    return parser.parse_args()

def main():
    args = parse_args()
    if args.diagnostics:
        print(run_diagnostics_text(os.getcwd()))
        print(run_diagnostics_summary(os.getcwd()))
        return 0
    if args.gui:
        launch_gui(config_path=args.config)
        return 0
    if args.batch and args.input_folder:
        return run_batch_via_gui_contract(args.input_folder, args.config, progress_callback=print)
    if args.batch and args.input_file:
        cfg = load_config(args.config)
        pipe = Pipeline(cfg)
        report = pipe.process_episode(args.input_file, progress_callback=print)
        return 0 if report.get("generated_outputs") else 1
    launch_gui(config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
