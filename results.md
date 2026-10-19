| Part | Setup | URLs | Rate | Seconds | Seed | Run | Hit rate | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 1 | 0.917 | 1 | 303 |
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 2 | 0.917 | 2 | 304 |

## Measured on the published files

One run each on 18 and 19 October 2026, on a fresh clone built on a new three-node kind cluster (kind 0.32, Kubernetes 1.36.1, Varnish 7.7, HAProxy 3.2) with `kubectl apply -k .`. Every run is the same cycle: delete the last load, restart both caches so they start empty, wait until both routers see both caches up, start the load (200 URLs, 20/s, 360 s, 8 workers, seed 1). The kill and the drain happen 180 s in, in the quiet stretch between two waves of expiry.

| Run | What happened | Misses | Failed | Hit rate | Worst 10 s p95 ms |
|---|---|---|---|---|---|
| Router | Nothing: requests routed by URL | 600 | 0 | 0.917 | 318 |
| Plain Service | The same load sent to the `varnish` Service | 1178 | 0 | 0.836 | 304 |
| Kill | `varnish-1` deleted at 180 s | 728 | 0 | 0.899 | 304 |
| Drain | the worker holding `varnish-0`, one router and one origin, drained at 180 s | 699 | 9 | 0.902 | 1805 |
| Drain, again | the same | 742 | 11 | 0.895 | 1806 |

- **Routing by URL halves what reaches the origin**: 600 misses against 1178 for the same traffic, because each page lives in one cache instead of both.
- **Losing a cache costs its pages once**: 126 misses in the 60 s after the kill, against 3 in the same minute of the router run, and no failed request.
- **A drain under load failed 9 and 11 requests** in two runs, all within 40 s of the drain except 3. Unlike the kill, a drain also evicts a router, and the routers that stayed up logged no failures, so the requests were lost on the way through the evicted router or to it. That is the likeliest reading and it is not traced.
- An earlier try at the router run, with the load started before the routers had marked a restarted cache up again, gave 611 misses: its first pages went to the other cache.

Your numbers will differ by a few; the shape is what to read.
