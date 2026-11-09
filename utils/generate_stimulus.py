import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

RQ2_VIDEO_DIR = Path("rq2_videos")
csv_path = Path("utils/data_with_cloudflare.csv")
output_path = Path("stimuli_from_catalog.json")
output_path_rq2 = Path("stimuli_from_rq2.json")

videos = []

with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
    for row in csv.DictReader(csv_file):
        video_id = urlsplit(row["URL"]).path.rstrip("/").split("/")[-1]
        query_ranges = json.loads(row["Query Ranges"])

        videos.append({
            "id": video_id,
            "title": row["Title"].strip(),
            "video_url": row["Cloudflare URL"],
            "payoff_label": row["Payoff Label"].strip(),
            "probe_candidates": [
                {
                    "start_s": candidate["start_s"],
                    "end_s": candidate["end_s"],
                    "category": candidate["kind"],
                }
                for candidate in query_ranges
            ],
        })

rq2_videos = []
RQ2_PATH_PREFIX = "https://pub-ac0b745d1d8e43f28f455b068d4b59a0.r2.dev/rq2_videos/"

# add video titles from RQ2_VIDEO_DIR
for video in RQ2_VIDEO_DIR.glob("*.mp4"):
    rq2_videos.append({
        "id": video.stem,
        "title": video.stem,
        "video_url": RQ2_PATH_PREFIX + video.name,
        "category": "RQ2"
    })

with output_path.open("w", encoding="utf-8") as output_file:
    json.dump({"videos": videos}, output_file, indent=2, ensure_ascii=False)
    output_file.write("\n")



print(f"Wrote {len(videos)} videos to {output_path}")
with output_path_rq2.open("w", encoding="utf-8") as output_file_rq2:
    json.dump({"videos": rq2_videos}, output_file_rq2, indent=2, ensure_ascii=False)
    output_file_rq2.write("\n")
print(f"Wrote {len(rq2_videos)} videos to {output_path_rq2}")
