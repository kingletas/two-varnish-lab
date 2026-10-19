# Part 4: kill one under load

Part 3 replaced a cache with nothing running and watched which pages survived. This part does it under load, and then does what a node's maintenance does: a drain. **Every run here is the same six-minute load from Part 2, so the only thing that changes between two runs is the thing you changed.** You start with a timeline in the load, so a run shows *when* something happened and not only how much.

By the end you have the third number, two changes to the router's config for losing a cache, pods spread across both workers, a budget that says how many of each may go at once, and a router that finishes its requests before it stops.

## 1. Give the load a timeline

The load prints totals. A failure lasts seconds, and a total over six minutes hides it. Change `load/load.py` so each result also remembers when its request started, and the script prints one line per ten seconds at the end. Type the whole file again:

```python
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
```

```bash
ruff check load/load.py
```

```bash
ruff format --check load/load.py
```

**Why:**

- **`WINDOW` is the timeline's step:** 10 seconds unless the Job sets it, and `load/job.yaml` doesn't.
- **`start` moved into `main`.** Before, each worker kept its own clock. Now all eight share one, so "second 185" means the same moment in every worker's results.
- **`other` is everything that's neither a hit nor a miss:** a request that timed out, a refused connection, an error page. In every run so far it was 0. In this part it's the number that matters most.
- **A worker waits for its own request.** If one request hangs for five seconds, that worker's next requests start late and then catch up in a burst. A window with fewer than 200 requests, followed by one with more, is that worker catching up. You'll see it.

## 2. The baseline, with its shape

The measuring cycle from the tightening section's step 7, nothing else changed. Repeat the status command until all four server lines say `UP` before you start the load:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

```bash
kubectl apply -k load/
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

**You should see** the same totals as before, and then the timeline (shortened here):

```text
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6600 miss=600
other=0
hit_rate=0.917
p50_ms=3
p95_ms=304
window_s requests miss other p95_ms
       0      200  126     0    318
      10      200   39     0    305
      20      200   23     0    304
      30      200    9     0      5
      40      200    2     0      6
      50      200    0     0      4
     ...
     120      200   47     0    321
     130      200   63     0    312
     140      200   42     0    305
     150      200   23     0    304
     160      200   12     0    303
     170      200   10     0    303
     180      200    1     0      4
     190      200    0     0      4
     200      200    0     0      4
     210      200    1     0      3
     220      200    1     0      3
     230      200    0     0      3
     240      200   10     0    303
     250      200   41     0    303
     260      200   54     0    304
     270      200   43     0    303
     280      200   26     0    303
     290      200   14     0    303
     300      200    7     0      4
     ...
```

**600 and 0.917, the same as every run since Part 2.** The timeline is new, and it has a shape.

**Why:**

- **Misses come in waves, two minutes apart.** The origin says `s-maxage=120`, and grace is 0, so every page expires two minutes after it was fetched, and the next request for it is a miss. The first wave fills the caches; each later wave is the same pages expiring again, a little more spread out each time.
- **Between the waves, almost nothing misses.** The six windows from 180 to 230 seconds show 1, 0, 0, 1, 1 and 0 misses. **That quiet stretch is where you kill a cache**, so nearly every extra miss is the kill's and not a wave's.
- **The p95 column follows the misses.** When 10 or more of a window's 200 requests miss, its p95 is a miss: between 303 and 321 ms in this run, the origin's 300 ms plus a little. With 9 or fewer it's a hit, in single digits: the window at 30 seconds has 9 misses and a p95 of 5.

Write down this run's ConfigMap name from `kubectl get configmap`, as in the tightening section. Every run in this part uses the same script.

## 3. Kill a cache in the quiet stretch

Start a fresh cycle: clear the last Job, make both caches cold, and repeat the status command until all four server lines say `UP`:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

Start the load, and note the time:

```bash
kubectl apply -k load/
```

Three minutes after you started it, delete the cache Part 3 step 10 showed owns `/p/7`:

```bash
kubectl delete pod varnish-1 --wait=false
```

Straight after, watch what both routers believe about it, every two seconds:

```bash
for i in $(seq 1 10); do for p in $(kubectl get pods -l app=router -o name); do kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | awk -F, '$2=="varnish-1" {printf "%s  ", $18}'; done; echo; sleep 2; done
```

Then the end of the run:

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

**You should see** the routers say `UP` almost the whole time, perhaps an `UP 2/3` or `UP 1/3` on the way, and then this:

```text
requests=7200 hit=6478 miss=714
other=8
hit_rate=0.900
p50_ms=3
p95_ms=304
window_s requests miss other p95_ms
     ...
     170      200   10     0    302
     180      170   47     8    313
     190      230   49     0    304
     200      200   13     0    303
     210      200    6     0      5
     220      200    4     0      5
     230      200    0     0      4
     ...
     300      200    8     0      6
     310      200   62     0    305
     320      200   23     0    303
     ...
```

**114 more misses than the baseline, and 8 requests that failed.** This is one sample run, made while this guide was written with the 2-second connect timeout. The kill in [results.md](../../results.md) ran on the finished files and gave 728 misses, 0 failed and a hit rate of 0.899.

**Why:**

- **114 is close to the dead cache's pages.** `varnish-1` owns 117 of the 200 URLs (Part 3 step 11). Its cache was emptied, and the other cache lost nothing. The extra misses were 114, not 117, and nothing on this page explains the missing 3.
- **The third number.** 117 of 200 pages, 58.5%, went cold. Of the 114 extra misses, 109 landed in the 25 seconds after the delete, and 45 seconds after it the timeline was back to the baseline's.
- **The wave moved.** The pages refilled at second 185 expire at 305, which is the spike at 310 that the baseline doesn't have.
- **The 180 window has 170 requests and the 190 window has 230.** That's step 1's catching up: some requests hung, and their workers started their next ones late.
- **The routers never said `DOWN`.** The new `varnish-1` was running three seconds after the delete, before a third health check could fail. So the router did nothing wrong by its own rules, and 8 requests still failed. Step 4 finds out where.

## 4. Read what the router did with them

The router logs every request with five timers. Ask it for every request that took over a second, or didn't end in `200`:

```bash
kubectl logs -l app=router --tail=-1 | awk '{split($5,t,"/")} t[5] > 1000 || ($6 ~ /^[0-9]+$/ && $6 != 200)'
```

**You should see** eight lines like these, all in the second after the delete:

```text
10.244.2.8:54040 [13/Mar/2026:14:23:18.885] web caches/varnish-0 0/6003/0/302/6305 200 318 - - ---- 6/6/5/3/+3 0/0 "GET /p/135 HTTP/1.1"
10.244.2.8:54032 [13/Mar/2026:14:23:18.885] web caches/varnish-0 0/6005/0/302/6307 200 316 - - ---- 5/5/4/2/+3 0/0 "GET /p/81 HTTP/1.1"
10.244.2.8:54068 [13/Mar/2026:14:23:18.885] web caches/varnish-0 0/6006/0/301/6307 200 317 - - ---- 4/4/3/1/+3 0/0 "GET /p/93 HTTP/1.1"
```

**The router says `200`. The load says those requests failed. Both are right.**

**Why:**

- **Read the timers, `0/6003/0/302/6305`:** 0 ms reading the request, **6003 ms in the second timer, which HAProxy calls queue time**, 0 to connect, 302 for the answer, 6305 in all.

  The failed connects are counted as queue time because a redispatch puts the request back in the queue; the connect timer shows only the last connect, the one that worked at once. `+3` near the end means three retries, and the `+` means the last one went to another server.
- **Where the 6 seconds come from:** for a few seconds the router still had the dead pod's address. A connection to an address nobody answers on doesn't fail, it hangs, until `timeout connect 2s`. The first try and two retries each wait the full 2 seconds, which is the 6; the third retry is the one `option redispatch` moves to `varnish-0`, which fetches the page from the origin.
- **The load gives up at 5 seconds** (`timeout=5` in `load.py`). A person in a browser may give up sooner. So the router finished the job and logged a success one second after the client had stopped waiting. This is why the load counts on its own side.
- **Two seconds to connect is a setting for the internet.** Inside a cluster a connection takes well under a millisecond. Waiting two seconds for one only makes a dead pod expensive.

In `haproxy.cfg`, change the connect timeout from `2s` to `500ms`, so the `defaults` section reads:

```haproxy
defaults
    mode http
    log global
    option httplog
    option dontlognull
    option redispatch
    retries 3
    timeout connect 500ms
    timeout client 30s
    timeout server 30s
```

Check the file, apply it, and wait for the rollout:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/router
```

Now run the kill again: a fresh cycle, the delete at three minutes, the load's log, and the router's slow and failed requests. Repeat the status command until all four server lines say `UP` before you start the load.

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

```bash
kubectl apply -k load/
```

Three minutes after you started it:

```bash
kubectl delete pod varnish-1 --wait=false
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
kubectl logs -l app=router --tail=-1 | awk '{split($5,t,"/")} t[5] > 1000 || ($6 ~ /^[0-9]+$/ && $6 != 200)'
```

**You should see:**

```text
requests=7200 hit=6467 miss=729
other=4
hit_rate=0.898
     ...
     180      200   69     4   1808
     ...
```

and from the router command, the slow ones now take about 1.8 seconds and succeed, and four new lines. Two of them:

```text
10.244.2.12:57370 [13/Mar/2026:14:31:20.146] web caches/varnish-1 0/0/0/-1/13 502 209 - - SH-- 3/3/1/0/0 0/0 "GET /p/81 HTTP/1.1"
10.244.2.12:57346 [13/Mar/2026:14:31:20.146] web caches/varnish-1 0/0/0/-1/13 502 209 - - SH-- 3/3/1/0/0 0/0 "GET /p/93 HTTP/1.1"
```

**Why:**

- **The hanging requests are fixed.** Three tries at 500 ms is 1.5 seconds, then the other cache: slow, under the load's 5 seconds, answered.
- **The 4 are a different failure.** `502` with `SH--`: the server hung up while the router was waiting for its headers. These requests were *inside* `varnish-1`, waiting on the origin, when it was killed. Whether you get some depends on what was in flight at that instant, which is why the first run had none.
- **The router doesn't retry them by default.** Out of the box it only retries when it couldn't connect. Once a request has been sent, a retry could do something twice. **For a GET that's safe**, and every request here is a GET. HAProxy doesn't check the method, though: `retry-on` would replay a POST too. A router that ever carries one adds `http-request disable-l7-retry unless METH_GET`, the action HAProxy's manual names for this.

Add one line under `retries 3`, so the `defaults` section reads:

```haproxy
defaults
    mode http
    log global
    option httplog
    option dontlognull
    option redispatch
    retries 3
    retry-on conn-failure empty-response
    timeout connect 500ms
    timeout client 30s
    timeout server 30s
```

Your `haproxy.cfg` is now the same file as the repository's. Check it, apply it, and wait for the rollout:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/router
```

Then run the kill a third time, again waiting for four `UP` lines before you start the load:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

```bash
kubectl apply -k load/
```

Three minutes after you started it:

```bash
kubectl delete pod varnish-1 --wait=false
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

**You should see:**

```text
requests=7200 hit=6453 miss=747
other=0
hit_rate=0.896
     ...
     180      120   33     0   1811
     190      280   94     0    303
     200      200   14     0    303
     ...
```

**0 failed, out of 7,200.** 38 requests took up to 1.8 seconds while the router moved off the dead address, and every one was answered.

**Why:**

- **`conn-failure` is what `retries` did already; `empty-response` is the new part:** a server that closed the connection without an answer is now tried again, elsewhere.
- **The misses rose across these three runs** (714, 729, 747) while the failures fell (8, 4, 0). Part of that is the failures: a request that fails is neither a hit nor a miss, and once it's answered it can count as a miss. The rest is most likely the requests the router sent to `varnish-0` while `varnish-1` was gone, which filled those pages in `varnish-0` too, before `varnish-1` fetched them again.

  Three runs aren't a trend, though: Part 3 step 4's two runs of the same setup differed by six misses.
- **A drain ends a pod the same way.** `kubectl delete pod` sends the process a stop signal, and Varnish stops at once, with whatever it was doing. A drain, a rollout and a node going down for maintenance all send the same signal, so step 7 meets the same failures. The harshest case, a node that dies with no signal at all, isn't in this lab.

## 5. Spread each workload across the workers

The tightening section ended with a drain that took both origins and both routers at once. Nothing said where pods must go. Add a `topologySpreadConstraints` block to the pod `spec` of `02-origin.yaml`, above `containers:`, with `app: origin`. The pod `spec` now starts:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: origin
      containers:
```

The same block goes in `09-router.yaml`, with `app: router`:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: router
      containers:
```

And in `04-varnish.yaml`, with `app: varnish`:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: varnish
      containers:
```

Then:

```bash
kubectl apply -k .
```

```bash
kubectl get pods -o wide
```

**You should see** the new origin and router pods stay `Pending`, with the old ones still running. Ask one of them why:

```bash
kubectl describe pod -l app=origin | grep -A3 FailedScheduling
```

```text
  Warning  FailedScheduling  2m12s (x2 over 2m12s)  default-scheduler  0/3 nodes are available: 1 node(s) had untolerated taint(s), 2 node(s) didn't match pod topology spread constraints.
```

**The rollout is stuck, and will stay stuck.**

**Why:**

- **`maxSkew: 1` across `kubernetes.io/hostname`:** no node may have more than one pod of this kind beyond the node with the fewest.
- **The node with the fewest is the control plane.** It has a taint that keeps your pods off it, and by default the spread still counts it, as a node with zero. So the most any worker may hold is one. A rollout needs room for one extra pod while the old one is still running, and there's none.
- **`DoNotSchedule` means exactly that.** The other choice, `ScheduleAnyway`, would have placed the pod and treated the spread as a wish. Here a stuck rollout is the right outcome: it told you the rule was wrong before anything broke.

Tell the spread to leave out nodes your pods may not use. Add one line under `whenUnsatisfiable` in all three files:

```yaml
          nodeTaintsPolicy: Honor
```

In `02-origin.yaml` the pod `spec` now starts:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          labelSelector:
            matchLabels:
              app: origin
      containers:
```

In `09-router.yaml`:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          labelSelector:
            matchLabels:
              app: router
      containers:
```

And in `04-varnish.yaml`:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          labelSelector:
            matchLabels:
              app: varnish
      containers:
```

Apply, wait for the three rollouts, and look again:

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/origin
```

```bash
kubectl rollout status deployment/router
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
kubectl get pods -o wide
```

**You should see** everything `Running`. Now read the `NODE` column. In the run behind this guide:

```text
router-7957c7bbc7-hwpzd   1/1   Running   0   40s   10.244.2.21   lab-worker2
router-7957c7bbc7-pvlz2   1/1   Running   0   25s   10.244.2.22   lab-worker2
```

**Both routers on one worker, with a rule that says one each.**

**Why:** the spread counts every pod with `app: router`, the old ones too. The first new router was placed while an old one stood on each worker, so either worker kept the skew at 1. Then the old ones went away, one by one, and the rule is only checked when a pod is placed, never after. **A rolling update counts the revision it's replacing.**

Add two lines under `nodeTaintsPolicy` in `02-origin.yaml` and `09-router.yaml`, not in `04-varnish.yaml`:

```yaml
          matchLabelKeys:
            - pod-template-hash
```

In `02-origin.yaml` the pod `spec` now starts:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          matchLabelKeys:
            - pod-template-hash
          labelSelector:
            matchLabels:
              app: origin
      containers:
```

And in `09-router.yaml`:

```yaml
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          matchLabelKeys:
            - pod-template-hash
          labelSelector:
            matchLabels:
              app: router
      containers:
```

Apply, wait for the rollouts, and look again:

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/origin
```

```bash
kubectl rollout status deployment/router
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
kubectl get pods -o wide
```

**You should see** one origin and one router on each worker, and the two caches on different workers.

**Why:**

- **`pod-template-hash` is the label a Deployment puts on each revision's pods.** With it, the spread counts only the pods of the revision being placed, so the new ones spread among themselves.
- **Not on the StatefulSet.** It replaces one pod at a time, deleting the old one before making the new one, so it never has two revisions standing side by side, and it doesn't use that label.

## 6. A budget for how many may go at once

Type this as `11-disruption-budgets.yaml`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: origin
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: origin
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: varnish
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: varnish
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: router
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: router
```

Add `- 11-disruption-budgets.yaml` to the end of the `resources` list in `kustomization.yaml`. The file now reads, and it's the repository's final `kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: lab
resources:
  - 00-namespace.yaml
  - 02-origin.yaml
  - 03-origin-service.yaml
  - 04-varnish.yaml
  - 05-varnish-service.yaml
  - 08-varnish-pods-service.yaml
  - 09-router.yaml
  - 10-router-service.yaml
  - 11-disruption-budgets.yaml
configMapGenerator:
  - name: origin-code
    files:
      - server.py
  - name: varnish-vcl
    files:
      - default.vcl
  - name: router-config
    files:
      - haproxy.cfg
```

Then:

```bash
kubectl apply -k .
```

```bash
kubectl get pdb
```

**You should see:**

```text
NAME      MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
origin    N/A             1                 1                     10s
router    N/A             1                 1                     10s
varnish   N/A             1                 1                     10s
```

**Why:** a budget is a promise to everything that removes pods politely: of these, at most one may be gone at a time. `ALLOWED DISRUPTIONS` is how many may go right now. What "politely" means is the next two steps.

## 7. Drain a worker under load

Start a fresh cycle: clear the last Job, make both caches cold, and repeat the status command until all four server lines say `UP`:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

Find the worker `varnish-0` is on. It's `lab-worker` in this run:

```bash
kubectl get pod varnish-0 -o jsonpath='{.spec.nodeName}{"\n"}'
```

Start the load, and note the time:

```bash
kubectl apply -k load/
```

Three minutes after you started it, drain that worker:

```bash
kubectl drain lab-worker --ignore-daemonsets --delete-emptydir-data
```

```bash
kubectl get pods -o wide
```

**You should see** the drain finish in about two seconds, one origin, one router and `varnish-0` evicted, and all three running again on `lab-worker2` within six seconds. Then wait for the run, and give the worker back:

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
kubectl uncordon lab-worker
```

```bash
kubectl get pods -o wide
```

```text
requests=7200 hit=6484 miss=716
other=0
hit_rate=0.901
p50_ms=4
p95_ms=304
     ...
     180      163   35     0   1816
     190      237   56     0    310
     200      200   19     0    305
     ...
```

**Half the lab's pods, three of six, were taken off a node in the middle of the load, and in this sample run no request failed.** And after the uncordon, every pod is still on `lab-worker2`.

**Why:**

- **The drain cost the same kind of thing as the kill:** a few seconds of 1.8-second answers, and the lost cache's pages plus about 30 more. The drain took `varnish-0`, which owns 83 pages, and cost 116 extra misses; the kill with `retry-on` took `varnish-1`, which owns 117, and cost 147.

  The extra 30 or so are pages the other cache filled while one was gone, fetched again when it came back. To the router a drained cache and a killed one look the same, and the two lines from step 4 are what kept the failures at 0.
- **The router you drained didn't fail anybody in this run.** The load reaches the routers through their Service, and no request failed while one of the two was evicted. That was the timing, not the design: the same drain can fail requests, and step 8 shows why.
- **The pods could go to `lab-worker2` because the drain cordoned `lab-worker`,** and `nodeTaintsPolicy: Honor` leaves a cordoned node out of the count, the same way it leaves out the control plane.
- **Nothing moves back.** The spread is checked when a pod is placed. After the uncordon, `lab-worker` is empty and stays empty until something replaces a pod. Kubernetes has no built-in step that rebalances running pods; that job is a separate tool, the descheduler, which this lab doesn't use.

## 8. Let the router finish before it stops

The drain in step 7 failed nothing, and that was partly luck. Two more drains of the same kind, on files the same as yours are now, failed 9 and 11 requests out of 7,200. They're the runs `Drain, before preStop` and `Drain, again` in [results.md](../../results.md), and their worst 10-second p95 was 1,805 and 1,806 ms.

The failed requests weren't logged anywhere: the routers that stayed logged only answers that worked. What follows is the most likely reading, which the fix supports. Nobody saw it happen.

When a drain evicts a pod, Kubernetes removes the pod's endpoint from the Service and signals the pod at the same moment. The HAProxy image's stop signal is `SIGUSR1`, a soft stop that closes the listener at once, so requests still on their way to that pod fail. A 5-second sleep before the signal lets the endpoint removal reach every client first.

In `09-router.yaml`, add two things. `terminationGracePeriodSeconds: 30` goes at the top of the pod `spec`, above `topologySpreadConstraints`. A `lifecycle` block with a `preStop` sleep goes in the container, between `args` and `env`. The two pieces, with their comments:

```yaml
      # The default, written out: it must outlast the preStop sleep below.
      terminationGracePeriodSeconds: 30
```

```yaml
          # A stopping router keeps answering for 5 s, while the Service stops
          # sending it new requests, before HAProxy is told to stop.
          lifecycle:
            preStop:
              sleep:
                seconds: 5
```

The whole file now reads, and it's the repository's final `09-router.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: router
  labels:
    app: router
spec:
  replicas: 2
  selector:
    matchLabels:
      app: router
  template:
    metadata:
      labels:
        app: router
    spec:
      # The default, written out: it must outlast the preStop sleep below.
      terminationGracePeriodSeconds: 30
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          nodeTaintsPolicy: Honor
          matchLabelKeys:
            - pod-template-hash
          labelSelector:
            matchLabels:
              app: router
      containers:
        - name: haproxy
          image: haproxy:3.2-alpine
          args: ["haproxy", "-f", "/cfg/haproxy.cfg"]
          # A stopping router keeps answering for 5 s, while the Service stops
          # sending it new requests, before HAProxy is told to stop.
          lifecycle:
            preStop:
              sleep:
                seconds: 5
          env:
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
          ports:
            - name: http
              containerPort: 8080
            - name: stats
              containerPort: 8404
          volumeMounts:
            - name: cfg
              mountPath: /cfg
              readOnly: true
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
            timeoutSeconds: 1
          livenessProbe:
            tcpSocket:
              port: http
            periodSeconds: 10
            timeoutSeconds: 1
            failureThreshold: 6
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 128Mi
      volumes:
        - name: cfg
          configMap:
            name: router-config
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/router
```

**Why:**

- **`preStop` runs before the stop signal is sent,** and the signal waits until it ends. `sleep` is an action Kubernetes runs itself, so the image needs no shell for it.
- **The grace period counts the sleep.** 30 seconds is the default, written out because it has to be longer than the 5-second sleep plus HAProxy's own stop. A grace period shorter than the sleep would end in a kill.
- **This is the other half of the graceful shutdown from Part 1 step 9.** The origin learned to stop when asked. The router now also finishes what still reaches it before it stops.

Now drain under load again. Start a fresh cycle, and repeat the status command until all four server lines say `UP`:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart statefulset/varnish
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

Find the worker `varnish-0` is on:

```bash
kubectl get pod varnish-0 -o jsonpath='{.spec.nodeName}{"\n"}'
```

Start the load, and note the time:

```bash
kubectl apply -k load/
```

Three minutes after you started it, drain that worker. Use the name the command above printed; it was `lab-worker` here:

```bash
kubectl drain lab-worker --ignore-daemonsets --delete-emptydir-data
```

Then wait for the run, read it, and give the worker back:

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
kubectl uncordon lab-worker
```

**You should see** `other=0`. On this repository's files, the two drains after this change, `Drain, router preStop` and `Drain, router preStop, again` in [results.md](../../results.md), failed none, against 9 and 11 before it. Their misses were 613 and 729, so read the failures, not the misses: the misses move from run to run.

In those runs, the evicted router logged its last request as it was evicted and stopped cleanly 6 seconds later. The worst 10-second p95 was 316 and 309 ms, against 1,805 and 1,806 before.

## 9. What a budget stops, and what it doesn't

Everything is on one worker now, `lab-worker2` in this run. Drain it, with no load running:

```bash
kubectl drain lab-worker2 --ignore-daemonsets --delete-emptydir-data
```

**You should see**, among the lines:

```text
error when evicting pods/"varnish-1" -n "lab" (will retry after 5s): Cannot evict pod as it would violate the pod's disruption budget.
error when evicting pods/"origin-79fbccd9bc-n2542" -n "lab" (will retry after 5s): Cannot evict pod as it would violate the pod's disruption budget.
pod/origin-79fbccd9bc-dp9zg evicted
pod/varnish-0 evicted
...
pod/varnish-1 evicted
node/lab-worker2 drained
```

Then give it back, and delete both caches at once:

```bash
kubectl uncordon lab-worker2
```

```bash
kubectl delete pod varnish-0 varnish-1 --wait=false
```

```bash
kubectl get pdb varnish
```

```text
pod "varnish-0" deleted from lab namespace
pod "varnish-1" deleted from lab namespace
NAME      MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
varnish   N/A             1                 0                     10m
```

**The drain waited its turn. The delete didn't ask.**

**Why:**

- **A drain evicts, and an eviction asks the budget first.** With both of a kind on one node, the budget allowed one; the second was refused, retried every five seconds, and let go once its partner was running again on `lab-worker`. A whole node was emptied without either cache, origin or router ever being down to zero.
- **`kubectl delete pod` doesn't evict.** It removes the pod, and the budget isn't consulted. `ALLOWED DISRUPTIONS 0` is the budget noticing afterwards. So a budget protects you from maintenance, from the cluster's own tools and from anyone using `drain`. It doesn't protect you from a person, a script, or a node that simply dies.
- **A drain also evicts the finished load pod** if it's on that node. That's harmless; the next cycle deletes the Job anyway.

## 10. Write the numbers down

Add a section to `results.md`, under the table. The numbers below are this guide's sample runs; put yours in their place:

```markdown
## Part 4: one cache lost at 185 s, under the same load

| Run | What happened | Misses | Failed | Hit rate | Worst 10 s p95 ms |
|---|---|---|---|---|---|
| Baseline | Nothing | 600 | 0 | 0.917 | 321 |
| Kill | `varnish-1` deleted, connect 2s | 714 | 8 | 0.900 | 320 |
| Kill | connect 500ms | 729 | 4 | 0.898 | 1808 |
| Kill | connect 500ms, retry-on | 747 | 0 | 0.896 | 1811 |
| Drain | `lab-worker` drained, spread and budgets | 716 | 0 | 0.901 | 1816 |
| Drain | the same, router `preStop` sleep 5 s | ... | ... | ... | ... |

The third number: killing the cache that owns 117 of the 200 pages sent 114 extra requests to the origin, 109 of them in the next 25 seconds; the timeline was back to the baseline's 45 seconds after the delete.
```

The last row has no sample value in this guide; this repository's runs of it are in [results.md](../../results.md).

**Why:** the worst-window p95 is the column the totals hide. In every sample run in this guide the totals' p95 is between 302 and 304 ms, and the Part 2 runs in results.md give 303 and 304. The failure only shows in the window it happened in.

## 11. Commit

```bash
git add load/load.py haproxy.cfg 02-origin.yaml 04-varnish.yaml 09-router.yaml 11-disruption-budgets.yaml kustomization.yaml results.md
```

```bash
git commit -m "The load prints a timeline; the router survives a lost cache with no failed request; pods spread across workers, with disruption budgets"
```

## What this doesn't cover

A cache that dies with its node and no warning, which kind can't really do; a load that isn't 200 URLs asked evenly; and a purge, which with two caches has to reach the one holding the page.

## Where your folder ends

Your folder now holds the same lab as this repository: compare any file with its copy here. The repository adds what publishing needs, among them a `Makefile`. `make check` runs, with no cluster, the checks you ran by hand: `ruff` on `server.py` and `load/load.py`, `kubectl kustomize` on each of the three folders, `varnishd` compiling the VCL, and `haproxy -c` on the router's config.

**Back to:** [the guide's contents](README.md), or the repository's [README](../../README.md) for every number with the setup it came from.
