| Part | Setup | URLs | Rate | Seconds | Seed | Run | Hit rate | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 1 | 0.917 | 1 | 303 |
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 2 | 0.917 | 2 | 304 |

## Measured on the published files

One run of each, the drains twice, on 18 and 19 October 2026, on a fresh clone built on a new three-node kind cluster (kind 0.32, Kubernetes 1.36.1, Varnish 7.7, HAProxy 3.2) with `kubectl apply -k .`. Every run is the same cycle: delete the last load, restart both caches so they start empty, wait until both routers see both caches up, start the load (200 URLs, 20/s, 360 s, 8 workers, seed 1). The kill and the drain happen 180 s in, in the quiet stretch between two waves of expiry.

| Run | What happened | Misses | Failed | Hit rate | Worst 10 s p95 ms |
|---|---|---|---|---|---|
| Router | Nothing: requests routed by URL | 600 | 0 | 0.917 | 318 |
| Plain Service | The same load sent to the `varnish` Service | 1178 | 0 | 0.836 | 304 |
| Kill | `varnish-1` deleted at 180 s | 728 | 0 | 0.899 | 304 |
| Drain, before preStop | the worker holding `varnish-0`, one router and one origin, drained at 180 s, before the router had its preStop sleep (18 October) | 699 | 9 | 0.902 | 1805 |
| Drain, again | the same | 742 | 11 | 0.895 | 1806 |
| Drain, router preStop | the same, after the router was given a 5 s preStop sleep (19 October) | 613 | 0 | 0.915 | 316 |
| Drain, router preStop, again | the same | 729 | 0 | 0.899 | 309 |

- **Routing by URL halves what reaches the origin**: 600 misses against 1178 for the same traffic, because each page lives in one cache instead of both.
- **Losing a cache costs its pages once**: 126 misses in the 60 s after the kill, against 3 in the same minute of the router run, and no failed request.
- **A drain under load failed 9 and 11 requests** in two runs, and **none once the router had a preStop sleep**, in two more. A drain evicts a router as well as a cache, and Kubernetes takes that pod out of the Service and signals it at the same moment. This image's stop signal is HAProxy's soft stop, which closes the listener at once, so requests the Service still sent it in that moment were most likely refused. The refusals themselves weren't logged.
- **With a 5 s sleep before the signal**, the evicted router logged its last request as it was evicted and stopped cleanly 6 s later. The worst 10 s window fell from about 1800 ms to about 310.
- An earlier try at the router run, with the load started before the routers had marked a restarted cache up again, gave 611 misses: its first pages went to the other cache.

Your numbers will differ by a few; the shape is what to read.
