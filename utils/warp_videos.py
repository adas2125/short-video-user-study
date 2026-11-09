import argparse
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

# Each entry is (filename, payoff start, payoff end) in source seconds.
# None means the payoff runs to the end of the file.
PAYOFFS = {
    "Tiny_House": (
        "Incredible blue tiny house made with mini bricks #miniature #diy.mp4",
        65, None,
    ),
    "Chocolate_Cake": (
        "Ultimate chocolate moist cake #shorts #cooking #recipe.mp4",
        45, None,
    ),
    "Rug_Restoration": (
        "So Dirty, So Satisfying – Deep Clean Restoration.mp4",
        51, None,
    ),
    "Tin_Foil_Experiment": (
        "Testing Tin Foil Science Experiment.mp4",
        0, 11,
    ),
    "Alfredo_Chicken_Parmesan": (
        "Making Alfredo Chicken Parmesan For My Picky Kids #shorts #cooking #recipe.mp4",
        0, 6,
    ),
    "Super_Elytra": (
        "Super Elytra! #shorts #minecraft.mp4",
        0, 8,
    ),
}

TRIM_START = {"Alfredo_Chicken_Parmesan": 0}

def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", *args], check=True)

def render_pair(source, name, payoff_start, payoff_end, output_dir):
    start = TRIM_START.get(name, 0)
    payoff = [(payoff_start, payoff_end)]
    rest = []
    if start < payoff_start:
        rest.append((start, payoff_start))
    if payoff_end is not None:
        rest.append((payoff_end, None))

    with TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        parts = {}
        for i, (start, end) in enumerate(rest + payoff):
            part = temp_dir / f"part_{i}.mp4"
            cut = ["-ss", str(start), "-i", str(source)]
            if end is not None:
                cut.extend(["-t", str(end - start)])
            ffmpeg(
                "-threads", "2", *cut,
                "-c:v", "libx264", "-preset", "veryfast",
                "-threads", "2", "-c:a", "aac",
                str(part),
            )
            parts[start, end] = part.name

        for version, segments in [
            ("Early", payoff + rest),
            ("Late", rest + payoff),
        ]:
            playlist = temp_dir / f"{version}.txt"
            playlist.write_text(
                "".join(f"file '{parts[segment]}'\n" for segment in segments),
                encoding="utf-8",
            )
            output = output_dir / f"{name}_{version}_Payoff.mp4"
            ffmpeg("-f", "concat", "-i", str(playlist), "-c", "copy",
                   "-movflags", "+faststart", str(output))
            print(f"Wrote {output}")


def main():
    parser = argparse.ArgumentParser(description="Render the RQ2 payoff edits.")
    parser.add_argument("source_dir", nargs="?", type=Path, default=Path("payoff_original_videos"))
    parser.add_argument("--output-dir", type=Path, default=Path("rq2_videos"))
    parser.add_argument("--clip", choices=PAYOFFS, help="Render only this pair.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ([args.clip] if args.clip else PAYOFFS):
        filename, payoff_start, payoff_end = PAYOFFS[name]
        source = args.source_dir / filename
        if not source.is_file():
            parser.error(f"Source MP4 not found: {source}")
        render_pair(source, name, payoff_start, payoff_end, args.output_dir)


if __name__ == "__main__":
    main()