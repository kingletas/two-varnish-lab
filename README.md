# two-varnish-lab

[![CI](https://github.com/kingletas/two-varnish-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/kingletas/two-varnish-lab/actions/workflows/ci.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

**What happens when you run two Varnish caches on Kubernetes instead of one, and how do you keep them from doing each other's work?**

One Varnish in front of a monolith like Magento is a single point of failure: when it goes down, every request lands on the application at once. Run two, and that's gone. But two caches don't share anything. Put them behind a plain Kubernetes Service and each one caches every page for itself, so the application fills the cache twice.

This lab puts two Varnish caches behind HAProxy, which sends each URL to the same cache every time, and measures what that's worth: in normal running, when a cache dies, and when a node is drained.

> [!NOTE]
> **This is test and research, nothing production-like.** It runs on a three-node [kind](https://kind.sigs.k8s.io/) cluster on a laptop. The origin is an invented web app that takes 300 ms per page, and the load is invented too. Nothing here is built or hardened for production.

## What it found

One run each (the drains twice), on a fresh clone and a new cluster. The load asks for 200 pages at 20 requests a second for six minutes. The kill and the drain happen 180 seconds in.

| Run | What happened | Misses | Failed | Hit rate |
|---|---|---|---|---|
| Router | Each URL goes to the same cache | 600 | 0 | 0.917 |
| Plain Service | The same load, sent to either cache | 1178 | 0 | 0.836 |
| Kill | `varnish-1` deleted mid-run | 728 | 0 | 0.899 |
| Drain, before preStop | A worker drained mid-run, before the router had its `preStop` sleep | 699 | 9 | 0.902 |
| Drain, again | The same | 742 | 11 | 0.895 |
| Drain, router preStop | The same, after the router got a 5 s `preStop` sleep | 613 | 0 | 0.915 |
| Drain, router preStop, again | The same | 729 | 0 | 0.899 |

- **Routing by URL halves what reaches the origin.** 600 misses against 1178 for the same traffic, because each page lives in one cache instead of both. 600 is as low as it goes: the origin lets a page be cached for two minutes, so each of the 200 pages is fetched once every two minutes.
- **Losing a cache costs its pages once, and fails nothing.** 126 misses in the minute after the kill, against 3 in the same minute of the router run. The StatefulSet brings `varnish-1` back empty under the same name within seconds, and its pages are fetched once more.
- **A drain under load failed 9 and 11 requests; with a 5 s `preStop` sleep on the router, two more drains failed none.** When Kubernetes evicts a pod, it takes the pod out of the Service and sends it the stop signal at the same moment, and the two race. For a second or so the Service can still send new requests to a router whose HAProxy has already closed its listener, and those were most likely the ones refused. The sleep makes the pod wait before it gets the signal, so by the time HAProxy stops listening nobody is sending to it. The grace period has to be longer than the sleep.

Your numbers will differ by a few. The shape is what to read. Every run, with its setup, is in [results.md](results.md).

## Run it

[docs/from-nothing.md](docs/from-nothing.md) goes from an empty laptop to both hit rates, with the output you should see at every step. The short version:

```bash
kind create cluster --name lab --config kind-cluster.yaml
```

```bash
kubectl apply -k .
```

```bash
kubectl -n lab wait --for=condition=Ready pod --all --timeout=300s
```

```bash
kubectl -n lab apply -k load/
```

```bash
kubectl -n lab wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl -n lab logs job/load
```

If you'd rather build it than run it, [docs/guide/](docs/guide/README.md) has you type every file yourself, one part at a time, with the output you should see at each step.

## What it doesn't show

- **More than one run of each.** Each drain ran twice; everything else once.
- **A cache dying with its node, with no warning.** kind can't pull the power on a node, so the kill here is a deleted pod.
- **Real traffic.** The load asks for 200 pages evenly. A real store has a few pages everyone wants and a long tail nobody does.
- **Invalidation.** With two caches, a purge has to reach the one holding the page, so Magento has to list both. The lab sends no purge.
- **The refused requests themselves.** They never reached HAProxy, so no log shows them. "Refused" is the reading the fix supports, not something seen.

## What's in here

| File | What it is |
|---|---|
| `kind-cluster.yaml` | One control plane and two workers, with the node image pinned |
| `server.py`, `02-origin.yaml`, `03-origin-service.yaml` | The invented origin: two replicas, 300 ms per page, cacheable for 120 s |
| `default.vcl`, `04-varnish.yaml` | Two Varnish caches as a StatefulSet, so each has a stable name: `varnish-0` and `varnish-1` |
| `08-varnish-pods-service.yaml` | A headless Service, so HAProxy can reach each cache by its own name |
| `05-varnish-service.yaml` | The plain Service in front of both caches, for the comparison run |
| `haproxy.cfg`, `09-router.yaml`, `10-router-service.yaml` | The router: two HAProxy replicas hashing each URL to one cache, with health checks, a retry when a connection fails, and a 5 s `preStop` sleep so a drain doesn't refuse requests |
| `11-disruption-budgets.yaml` | Pod disruption budgets, so a drain can't take out every copy of anything at once |
| `load/` | The load: a Job that sends requests through the router and prints the hit rate |
| `load-via-service/` | The same load, sent to the plain Service |
| `results.md` | Every measured run |

The spread rules keep the two copies of each piece on different workers, and the caches get a patient liveness probe, so a slow moment doesn't restart a full cache.

## Checking a change

```bash
make check
```

It needs no cluster: the manifests are rendered, not applied, and the VCL and the HAProxy config are checked by the same images the lab runs. CI runs the same check.

## License

[MIT](LICENSE)
