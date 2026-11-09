from collections import Counter
from random import Random
from itertools import combinations

CLASS_LABELS = {"loops_only": "L", "hybrid_loops_to_terminal": "L", "terminal_primary": "T"}
CLIPS_PER_LABEL = {"L": 6, "T": 6}

def select_rq2_versions(videos, rng):
    """Chooses the RQ2 versions for each video, ensuring one Early and one Late version per title."""
    pairs = {}
    for video in videos:
        base_id, timing, suffix = video["id"].rsplit("_", 2)
        if video.get("category") != "RQ2" or timing not in {"Early", "Late"} or suffix != "Payoff":
            raise ValueError(f"Invalid RQ2 entry: {video['id']}")

        versions = pairs.setdefault(base_id, {})
        if timing in versions:
            raise ValueError(f"Duplicate RQ2 version: {video['id']}")
        versions[timing] = video

    if len(pairs) != 6 or any(set(versions) != {"Early", "Late"} for versions in pairs.values()):
        raise ValueError("Expected six RQ2 titles with one Early and one Late version each")

    selected = []
    for base_id, versions in pairs.items():
        video = rng.choice((versions["Early"], versions["Late"])).copy()
        video["title"] = base_id.replace("_", " ")
        video["interaction_mode"] = "free_swipe"
        selected.append(video)

    rng.shuffle(selected)
    return selected

def assign_feed(stimuli, rng, *, rq2_stimuli=()):
    """Balance adjacent RQ1 transitions within this feed."""
    pools = {label: [] for label in CLIPS_PER_LABEL}
    for stimulus in stimuli:
        pools[CLASS_LABELS[stimulus["payoff_label"]]].append(stimulus)

    rq1_size = sum(CLIPS_PER_LABEL.values())
    feed_size = rq1_size + len(rq2_stimuli)
    rq2_positions = set(rng.sample(range(feed_size), len(rq2_stimuli)))
    rq1_positions = [
        position for position in range(feed_size)
        if position not in rq2_positions
    ]

    adjacent_rq1_indices = [
        index
        for index, (left, right) in enumerate(zip(rq1_positions, rq1_positions[1:]))
        if right == left + 1
    ]

    best_orders = []
    best_score = float("inf")

    # Each choice of L positions determines one six-L, six-T order.
    for loop_indices in combinations(range(rq1_size), CLIPS_PER_LABEL["L"]):
        order = ["L" if index in loop_indices else "T" for index in range(rq1_size)]
        pairs = Counter(
            (order[index], order[index + 1])
            for index in adjacent_rq1_indices
        )
        score = sum(count ** 2 for count in pairs.values())

        if score < best_score:
            best_orders = [order]
            best_score = score
        elif score == best_score:
            best_orders.append(order)

    order = rng.choice(best_orders)

    selected = {
        label: iter(rng.sample(pools[label], count))
        for label, count in CLIPS_PER_LABEL.items()
    }
    rq1_clips = (next(selected[label]).copy() for label in order)
    rq2_clips = iter(rq2_stimuli)

    # returns a list of the clips for this feed
    return [
        next(rq2_clips) if position in rq2_positions else next(rq1_clips)
        for position in range(feed_size)
    ]

def plan_probes(stimulus):
    if stimulus["interaction_mode"] != "fixed_probe":
        return []

    rng = Random()
    ranges = sorted(
        (item for item in stimulus["probe_candidates"] if item["start_s"] < item["end_s"]),
        key=lambda item: item["start_s"],
    )

    if stimulus["payoff_label"] == "terminal_primary":
        development = [item for item in ranges if item["category"] == "development"]
        buildup = [item for item in ranges if item["category"] == "buildup"]
        checkpoints = []

        if development:
            first = development[0]
            start, end = first["start_s"], first["end_s"]
            checkpoints.append({
                "time": start + (end - start) / 6,
                "category": first["category"],
                "stage": "earlier",
            })

        late_ranges = buildup or development
        if late_ranges:
            last = late_ranges[-1]
            start, end = last["start_s"], last["end_s"]
            checkpoints.append({
                "time": end - (end - start) / 6,
                "category": last["category"],
                "stage": "later",
            })

        return [rng.choice(checkpoints)] if checkpoints else []

    loops = [item for item in ranges if item["category"] == "loop"]
    eligible_starts = [
        current["start_s"]
        for previous, current in zip(loops, loops[1:])
        if previous["end_s"] <= current["start_s"]
    ]
    if len(eligible_starts) == 1:
        return [{"time": eligible_starts[0], "category": "loop", "stage": "single"}]
    if eligible_starts:
        time, stage = rng.choice([
            (eligible_starts[0], "earlier"),
            (eligible_starts[-1], "later"),
        ])
        return [{"time": time, "category": "loop", "stage": stage}]

    # Combine touching or overlapping blocks. Keep gaps between blocks separate.
    blocks = []
    for item in ranges:
        if item["category"] != "loop_block":
            continue
        start, end = item["start_s"], item["end_s"]
        if blocks and start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])

    stage, fraction = rng.choice([("earlier", 1 / 3), ("later", 2 / 3)])
    offset = sum(end - start for start, end in blocks) * fraction
    for start, end in blocks:
        length = end - start
        if offset < length:
            return [{"time": start + offset, "category": "loop_block", "stage": stage}]
        offset -= length

    return []
