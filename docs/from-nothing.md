# From nothing to a measured two-varnish-lab

By the end of this page you'll have two Varnish caches running on a Kubernetes cluster on your own laptop, and a number that says how well they share the work.

## Contents

- [What this is](#what-this-is)
- [What you need](#what-you-need)
- [Step 1: get the files](#step-1-get-the-files)
- [Step 2: a cluster](#step-2-a-cluster)
- [Step 3: the lab](#step-3-the-lab)
- [Step 4: measure it](#step-4-measure-it)
- [Step 5: the same load without routing by URL](#step-5-the-same-load-without-routing-by-url)
- [Step 6: clean up](#step-6-clean-up)
- [Where to go next](#where-to-go-next)

## What this is

One Varnish in front of a web application is a single point of failure: when it goes, every request lands on the application at once. Two Varnish behind a plain load balancer remove that, but each caches the same pages separately, so the application does twice the work to fill them. This lab puts two Varnish behind an HAProxy that sends each URL to the same cache every time, and measures what that's worth.

Everything here is test and research: a laptop cluster, an invented origin that takes 300 ms per page, and an invented load. Nothing in it is built or hardened for production.

## What you need

- Docker
- [kind](https://kind.sigs.k8s.io/) (this page was run with 0.32)
- kubectl (run with 1.36)
- About 1.6 GB of memory for the three nodes, and a few hundred MB of images the nodes pull on first use

## Step 1: get the files

```bash
git clone https://github.com/kingletas/two-varnish-lab && cd two-varnish-lab
```

## Step 2: a cluster

```bash
kind create cluster --name lab --config kind-cluster.yaml
```

**You should see** kind create one control plane and two workers, ending with `Set kubectl context to "kind-lab"`. The node image is pinned in `kind-cluster.yaml`, so your cluster runs the same Kubernetes as the one these numbers came from.

## Step 3: the lab

```bash
kubectl apply -k .
```

```bash
kubectl -n lab wait --for=condition=Ready pod --all --timeout=300s
```

**You should see** six pods `condition met`: two origins, two routers and two caches, `varnish-0` and `varnish-1`. The first time, this waits for the nodes to pull the images.

## Step 4: measure it

The load asks the routers for 200 pages, 20 requests a second for six minutes, and counts what the caches already had.

```bash
kubectl -n lab apply -k load/
```

```bash
kubectl -n lab wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl -n lab logs job/load
```

**You should see** this at the top, then a line for every 10 seconds of the run:

```text
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6600 miss=600
other=0
hit_rate=0.917
p50_ms=2
p95_ms=303
```

**600 misses is each of the 200 pages fetched once every two minutes**, which is as low as it can go: the origin lets a page be cached for 120 seconds. Each page lives in exactly one cache. Your numbers may differ by a few; the shape is what to read.

**To run it again**, delete the Job and restart the caches as in step 5, then give the routers ten seconds before `kubectl -n lab apply -k load/`. They check each cache every two seconds and want two good checks before they trust a restarted one; start sooner and its first pages go to the other cache. On the run behind this page that cost 11 extra misses: 611 instead of 600.

## Step 5: the same load without routing by URL

A load is a Job, and a finished Job isn't run again, so delete it first. Restarting the caches empties them, so the second run starts as cold as the first.

```bash
kubectl -n lab delete job load
```

```bash
kubectl -n lab rollout restart statefulset/varnish && kubectl -n lab rollout status statefulset/varnish
```

```bash
kubectl -n lab apply -k load-via-service
```

Then wait for it and read its log as in step 4.

**You should see:**

```text
requests=7200 hit=6022 miss=1178
other=0
hit_rate=0.836
```

**Twice the misses.** The plain Service sends each request to either cache, so each cache fetches most pages for itself. The origin did almost twice the work for the same traffic.

## Step 6: clean up

```bash
kind delete cluster --name lab
```

## Where to go next

- [README](../README.md): the question, and every number with the setup it came from
- `results.md`: the measured runs
- [CONTRIBUTING.md](../CONTRIBUTING.md)
