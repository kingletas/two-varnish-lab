"""Ask the cache for a fixed set of URLs at a fixed rate and count the hits."""

import os
import random
import threading
import time
import urllib.request
from collections import Counter

TARGET = os.environ["TARGET"]
URLS = int(os.environ["URLS"])
RATE = float(os.environ["RATE"])
SECONDS = int(os.environ["SECONDS"])
WORKERS = int(os.environ["WORKERS"])
SEED = int(os.environ["SEED"])
WINDOW = int(os.environ.get("WINDOW", "10"))


def work(index, start, results):
    rng = random.Random(SEED + index)
    interval = WORKERS / RATE
    for n in range(round(SECONDS / interval)):
        time.sleep(max(0, start + n * interval - time.monotonic()))
        url = f"{TARGET}/p/{rng.randrange(URLS)}"
        began = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=5) as reply:
                reply.read()
                outcome = reply.headers.get("X-Cache", "NONE")
        except OSError:
            outcome = "ERROR"
        results.append((outcome, time.monotonic() - began, began - start))


def p95(times):
    times = sorted(times)
    return times[int(len(times) * 0.95)] * 1000


def timeline(done):
    print("window_s requests miss other p95_ms", flush=True)
    for first in range(0, SECONDS, WINDOW):
        part = [item for item in done if first <= item[2] < first + WINDOW]
        if not part:
            continue
        counts = Counter(outcome for outcome, _, _ in part)
        other = len(part) - counts["HIT"] - counts["MISS"]
        late = p95([taken for _, taken, _ in part])
        print(f"{first:>8} {len(part):>8} {counts['MISS']:>4} {other:>5} {late:>6.0f}")


def main():
    results = [[] for _ in range(WORKERS)]
    start = time.monotonic()
    threads = [
        threading.Thread(target=work, args=(i, start, results[i]))
        for i in range(WORKERS)
    ]
    print(
        f"urls={URLS} rate={RATE}/s seconds={SECONDS} workers={WORKERS} seed={SEED}",
        flush=True,
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    done = [item for part in results for item in part]
    counts = Counter(outcome for outcome, _, _ in done)
    times = sorted(taken for _, taken, _ in done)
    total = len(done)
    print(f"requests={total} hit={counts['HIT']} miss={counts['MISS']}")
    print(f"other={total - counts['HIT'] - counts['MISS']}")
    print(f"hit_rate={counts['HIT'] / total:.3f}")
    print(f"p50_ms={times[total // 2] * 1000:.0f}")
    print(f"p95_ms={times[int(total * 0.95)] * 1000:.0f}")
    timeline(done)


if __name__ == "__main__":
    main()
