# Part 2: one Varnish in front

**What you have at the end:** one Varnish caching the origin, its config kept as a file in the folder and fed in through Kustomize, a VCL typo you broke on purpose and read about in the logs, and **the hit rate of one cache, measured twice: the baseline the lab's three numbers are compared with.**

## 1. Let Kustomize own the origin's code

*Kustomize* is built into `kubectl`. A file called `kustomization.yaml` lists the manifests and the files to turn into ConfigMaps, and `kubectl apply -k .` applies the whole folder as one unit. Its trick is the `configMapGenerator`: it names each ConfigMap after a hash of its content, so a change to the content is a change of name. We start by giving it the origin.

Type this as `kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - 02-origin.yaml
  - 03-origin-service.yaml
configMapGenerator:
  - name: origin-code
    files:
      - server.py
```

The generated copy from Part 1, `01-origin-code.yaml`, is now a second copy of `server.py` that could drift, so remove it from the folder and from git:

```bash
git rm 01-origin-code.yaml
```

Read what Kustomize would send before it sends anything:

```bash
kubectl kustomize . | grep 'origin-code'
```

**You should see:**

```text
  name: origin-code-hb96279hb7
          name: origin-code-hb96279hb7
```

Two lines, the same name, ending in a short run of letters and digits. That ending is worked out from the file's content and nothing else, so the same `server.py` gives the same name on any cluster. The first is the ConfigMap. The second is the Deployment's volume: you wrote `origin-code` in `02-origin.yaml`, and Kustomize rewrote it to the hashed name.

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/origin
```

```bash
kubectl get configmap
```

**You should see:**

```text
configmap/origin-code-hb96279hb7 created
service/origin unchanged
deployment.apps/origin configured
deployment "origin" successfully rolled out
NAME                     DATA   AGE
kube-root-ca.crt         1      33s
origin-code              1      32s
origin-code-hb96279hb7   1      11s
```

Two ConfigMaps whose names start with `origin-code`: the old plain one and the new hashed one. Nothing deletes the old one for you, so do it:

```bash
kubectl delete configmap origin-code
```

**Why:**

- **`kubectl kustomize .` only prints.** It's the dry run: you read the result before the cluster does.
- **The origin moves too, with one reason:** it has the same trap as Varnish's config (Part 1, step 9: a ConfigMap changed in place restarts nothing), and moving it makes `server.py` the only copy instead of a file you regenerate by hand.
- **The origin rolled once** because its pod template now names a different ConfigMap. Keep that in mind for step 6.
- **Tempted to skip the delete?** The old ConfigMap is harmless, but it's a file in the cluster that's in no file in your folder. The folder is the truth, so it goes.

## 2. Write the VCL

*VCL* is Varnish's configuration language: a small program that Varnish compiles and runs on every request. Each `sub` is a routine Varnish calls at a fixed moment in a request's life.

Type this as `default.vcl`:

```vcl
vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
}

sub vcl_recv {
    if (req.url == "/healthz") {
        return (synth(200, "ok"));
    }
}

sub vcl_deliver {
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
}
```

**Why:**

- **`backend origin`** points Varnish at the Service from Part 1 step 7, not at a pod. Pods change addresses; the Service name does not. Varnish looks the name up once, when the VCL loads.
- **`vcl_recv` runs when a request arrives.** For `/healthz`, Varnish answers by itself (`synth` makes a response up) and never asks the origin. A readiness check that depended on the origin would pull the cache out of service exactly when the origin is struggling, which is when the cache matters most.
- **Every other request falls through to Varnish's built-in rules.** They cache a plain GET that has no cookie, and they obey the origin's `s-maxage=120` from Part 1 step 2. We write nothing for that.

  Varnish's own default lifetime happens to be 120 seconds too, so the lab by itself can't show which of the two is at work. It was shown apart for this guide: with the default turned down to 5 seconds, a page was still a hit 8 seconds later, so the header is what counts.
- **`vcl_deliver` runs just before the reply leaves.** `obj.hits` is how many times this cached copy has been served before. `X-Cache` is our own header and not a standard one; it puts the answer to "was that a hit?" on every response.

## 3. The Varnish Deployment and its Service

Type this as `04-varnish.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: varnish
  labels:
    app: varnish
spec:
  replicas: 1
  selector:
    matchLabels:
      app: varnish
  template:
    metadata:
      labels:
        app: varnish
    spec:
      containers:
        - name: varnish
          image: varnish:7.7
          command: ["varnishd"]
          args:
            - "-F"
            - "-n"
            - "/tmp/varnish"
            - "-f"
            - "/vcl/default.vcl"
            - "-a"
            - "http=:8080,HTTP"
            - "-s"
            - "malloc,64m"
          ports:
            - name: http
              containerPort: 8080
          volumeMounts:
            - name: vcl
              mountPath: /vcl
              readOnly: true
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            failureThreshold: 3
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              memory: 256Mi
      volumes:
        - name: vcl
          configMap:
            name: varnish-vcl
```

Type this as `05-varnish-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: varnish
  labels:
    app: varnish
spec:
  selector:
    app: varnish
  ports:
    - name: http
      port: 80
      targetPort: http
```

Now teach Kustomize about both and about the VCL. Replace `kustomization.yaml` with:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - 02-origin.yaml
  - 03-origin-service.yaml
  - 04-varnish.yaml
  - 05-varnish-service.yaml
configMapGenerator:
  - name: origin-code
    files:
      - server.py
  - name: varnish-vcl
    files:
      - default.vcl
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/varnish
```

```bash
kubectl get pods -o wide
```

**You should see:**

```text
configmap/origin-code-hb96279hb7 unchanged
configmap/varnish-vcl-9k87kdmgcc created
service/origin unchanged
service/varnish created
deployment.apps/origin unchanged
deployment.apps/varnish created
deployment "varnish" successfully rolled out
NAME                       READY   STATUS    RESTARTS   AGE   IP           NODE          NOMINATED NODE   READINESS GATES
origin-75b8688f98-8kvrx    1/1     Running   0          27s   10.244.2.2   lab-worker2   <none>           <none>
origin-75b8688f98-wq4qz    1/1     Running   0          22s   10.244.1.4   lab-worker    <none>           <none>
varnish-7b8c6644dd-d982j   1/1     Running   0          15s   10.244.2.3   lab-worker2   <none>           <none>
```

One `varnish-…` pod next to the two origin pods. The origin pods didn't restart: every origin line says `unchanged`. Your pod names and addresses will differ.

**Why, block by block:**

- **`command` and `args` spell out the whole `varnishd` command line** instead of trusting the image's start script. A start script that changes between versions would change the port or the cache size under you, and the cache size is the number this step cares about.
- **`-F`** keeps `varnishd` in the foreground, which a container needs. **`-a http=:8080,HTTP`** listens on 8080, a port a non-root process may open, and the image does run as its own user, not root: `kubectl exec deploy/varnish -- id` answers `uid=1000(varnish)`. **`-n /tmp/varnish`** is the folder where Varnish keeps its working files and where `varnishstat` will look in step 5.
- **`-s malloc,64m`** gives the cache 64 MiB of memory. This is the choice that needs the most care, so read the next bullet slowly.
- **The cache size isn't the pod's memory.** 64m covers the cached pages and their headers. On top of that come the thread stacks, the per-request working memory, a separate store for short-lived objects, and the shared-memory log Varnish writes for tools like `varnishstat`.

  The memory limit counts all of it. A cache sized equal to or above the limit is killed the first time it fills: the kernel stops the container (`OOMKilled`, exit code 137) and the cache comes back empty.

  We leave room: a 256 MiB limit over a 64 MiB cache. That ratio is a starting guess for a small cache, not a law; step 10 reads what it really uses so you can see how much of the margin is left.
- **The same probe rules as the origin:** a named port, readiness every 5 seconds, liveness every 10 seconds, three misses to restart. Both ask `/healthz`, which Varnish answers itself (step 2).
- **Two things in a healthy pod look like trouble and aren't.** `kubectl describe pod` on the new pod lists two or three `Unhealthy` events from its first seconds, with `connection refused`: the probes asked before Varnish was listening, which is why a probe gets more than one try.

  And `kubectl logs deploy/varnish` shows a warning that `mlock() of VSM failed`: Varnish asked to pin a piece of memory, the container's allowance for that said no, and it carries on without.
- **`replicas: 1`** is on purpose. Part 3 adds the second one. One copy also means a restart empties the only cache there is; step 9 uses that.
- **The image is Docker's official `varnish`**, with the tag pinned to `7.7`. That tag follows the patch releases; the run behind this guide got 7.7.3.
- **`volumes` and `volumeMounts` are the same two halves as Part 1 step 6.** The file lands at `/vcl/default.vcl`, which is what `-f` reads. We mount a folder of our own rather than `/etc/varnish`, because mounting over `/etc/varnish` would hide the other files the image keeps there.

## 4. Ask for a page twice

Ask Varnish for the same page twice from inside the cluster, and keep only the lines that matter:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2; do wget -S -qO- http://varnish/shoes 2>&1 | grep -E "X-Cache|path="; done'
```

**You should see:**

```text
  X-Cache: MISS
path=/shoes pod=origin-75b8688f98-8kvrx made=00:20:11
  X-Cache: HIT
path=/shoes pod=origin-75b8688f98-8kvrx made=00:20:11
```

The second answer is the first one: same pod, same `made=` time. The origin wasn't asked.

**Why:**

- **`wget -S` prints the response headers**, to the error stream, so the `2>&1` joins them with the body before `grep` picks the lines.
- **A miss means Varnish asked the origin and kept the answer; a hit means it served its copy.** The header comes from step 2.
- **The `made=` time is the proof.** The origin stamps the second the page was made. A hit shows an old stamp, and nothing else in the answer could show that.

## 5. Read the counters inside the pod

`varnishstat` is Varnish's own counter reader, and it runs inside the pod, next to the process it reads. `kubectl exec` runs one command in a running container.

```bash
kubectl exec deploy/varnish -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss -f MAIN.client_req
```

**You should see:**

```text
MAIN.client_req              9         0.36 Good client requests received
MAIN.cache_hit               1         0.04 Cache hits
MAIN.cache_miss              1         0.04 Cache misses
```

`cache_hit` and `cache_miss` are 1 and 1, the two requests of step 4. `client_req` is higher than 2, because the cluster's probes count as requests too; yours depends on how long the pod has been up. The middle column is the rate per second since the pod started.

**Why:**

- **`-1` prints once and exits.** Without it `varnishstat` is a live screen, which doesn't work through `kubectl exec`. `-f` picks the counters by name and can be repeated.
- **`-n /tmp/varnish`** must match the `-n` in the Deployment. Without it `varnishstat` looks in the default place, finds nothing, and says `Could not get hold of varnishd, is it running?`
- **The hit rate is `cache_hit / (cache_hit + cache_miss)`.** Write that line down: it's the number the lab exists to produce.
- **Probes aren't in the hit rate.** They are answered by the VCL itself in step 2, so they count as client requests and as neither hit nor miss. This is the second reason to answer `/healthz` inside Varnish.
- **Counters start at zero when the pod starts.** That matters in step 9.

## 6. Change the VCL and watch it roll by itself

In Part 1 step 9 you changed a ConfigMap and nothing happened until `kubectl rollout restart`. Now change the VCL and see what Kustomize does instead.

Varnish's built-in rules keep a page for a short *grace* time after it expires (10 seconds by default): it serves the stale copy while it fetches a fresh one. That would make a page that the origin says lives 120 seconds count as a hit for about 130, and the measurement shouldn't carry a rule nobody asked about. Switch it off.

**This is a choice for the measurement, and the opposite of what production wants:** there, grace is what lets a cache keep serving a page while the origin is slow or down. In `default.vcl`, add this between the `backend` block and `vcl_recv`:

```vcl
sub vcl_backend_response {
    set beresp.grace = 0s;
}
```

The whole file now reads:

```vcl
vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
}

sub vcl_backend_response {
    set beresp.grace = 0s;
}

sub vcl_recv {
    if (req.url == "/healthz") {
        return (synth(200, "ok"));
    }
}

sub vcl_deliver {
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
}
```

```bash
kubectl apply -k .
```

```bash
kubectl get configmap
```

```bash
kubectl rollout status deployment/varnish
```

```bash
kubectl get pods
```

**You should see:**

```text
configmap/origin-code-hb96279hb7 unchanged
configmap/varnish-vcl-h8k97c96g7 created
service/origin unchanged
service/varnish unchanged
deployment.apps/origin unchanged
deployment.apps/varnish configured
NAME                     DATA   AGE
kube-root-ca.crt         1      76s
origin-code-hb96279hb7   1      54s
varnish-vcl-9k87kdmgcc   1      43s
varnish-vcl-h8k97c96g7   1      1s
deployment "varnish" successfully rolled out
NAME                       READY   STATUS        RESTARTS   AGE
origin-75b8688f98-8kvrx    1/1     Running       0          67s
origin-75b8688f98-wq4qz    1/1     Running       0          62s
varnish-65464df99b-vn8zq   1/1     Running       0          13s
varnish-7b8c6644dd-d982j   1/1     Terminating   0          55s
```

Two `varnish-vcl-…` ConfigMaps, and a `varnish-…` pod with a **different name and a young age**, while the old one is on its way out. You didn't run `rollout restart`.

**Why:**

- **The content changed, so the hash changed, so the name changed.** The Deployment's pod template now names a different ConfigMap, the controller sees a changed template, and it rolls the pods. In Part 1 the name stayed fixed, so the template didn't change and nothing rolled. A config change is now an ordinary release.
- **`vcl_backend_response` runs when Varnish has fetched a page from the origin.** `beresp.grace = 0s` says: when it expires, it's gone.
- **The old ConfigMap is still there.** Each version stays until you delete it, and so does each old ReplicaSet (the Deployment's record of one pod template). That's how step 7's fix will find the old state. `kubectl apply -k` never deletes what it no longer names.
- **The cache is gone.** The new pod starts empty. Remember that: a VCL change costs you every cached page.

## 7. Break it: a VCL that doesn't compile

Make a typo that no reader would catch by eye. In `default.vcl`, change `obj.hits` to `obj.hit` in the `if` line, so `vcl_deliver` reads:

```vcl
sub vcl_deliver {
    if (obj.hit > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
}
```

And apply:

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/varnish --timeout=30s
```

```bash
kubectl get pods
```

**You should see** the rollout give up, the old pod still `1/1`, and the new one failing over and over:

```text
Waiting for deployment "varnish" rollout to finish: 1 old replicas are pending termination...
error: timed out waiting for the condition
NAME                       READY   STATUS             RESTARTS      AGE
origin-75b8688f98-8kvrx    1/1     Running            0             97s
origin-75b8688f98-wq4qz    1/1     Running            0             92s
varnish-5fd5877cc7-sz2d6   0/1     CrashLoopBackOff   2 (17s ago)   30s
varnish-65464df99b-vn8zq   1/1     Running            0             43s
```

Prove the cache is still serving, from the old pod:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'wget -S -qO- http://varnish/shoes 2>&1 | grep -E "X-Cache|path="'
```

**You should see** an answer with an `X-Cache` line. It works.

Now find out why the new pod fails. There are three places to look. First, the pod's own record. Use the name of the failing pod from your own list, the one at `0/1`:

```bash
kubectl describe pod varnish-5fd5877cc7-sz2d6
```

**You should see**, in the middle, the container's state, and at the bottom, the events:

```text
    State:          Terminated
      Reason:       Error
      Exit Code:    2
    Last State:     Terminated
      Reason:       Error
      Exit Code:    2
...
Events:
  Warning  Unhealthy  0s (x4 over 34s)  kubelet  spec.containers{varnish}: Readiness probe failed: Get "http://10.244.2.4:8080/healthz": dial tcp 10.244.2.4:8080: connect: connection refused
  Warning  BackOff    0s (x7 over 32s)  kubelet  spec.containers{varnish}: Back-off restarting failed container varnish in pod varnish-5fd5877cc7-sz2d6_lab(fcf2c9a3-a738-464e-b2ad-4011f57552de)
```

This look caught the container in the middle of a try, so `State` says `Terminated`. Between tries it says `Waiting`, which is where the word `CrashLoopBackOff` in `kubectl get pods` comes from.

The namespace's recent events, in time order:

```bash
kubectl get events --sort-by=.lastTimestamp
```

**You should see** the same pod's warnings among the last lines. Both tell you that it crashed and that the cluster is waiting longer between tries. Neither tells you why. The program's own words are in the logs:

```bash
kubectl logs varnish-5fd5877cc7-sz2d6
```

**You should see** the compiler's message:

```text
Error:
Message from VCC-compiler:
Symbol not found: 'obj.hit'
At: ('/vcl/default.vcl' Line 19 Pos 9) -- (Pos 15)
    if (obj.hit > 0) {
--------#######-------

Running VCC-compiler failed, exited with 2
VCL compilation failed
```

The file, the line, the column, and the word underlined.

**Fix it the right way:** put `obj.hits` back in the file, so `vcl_deliver` reads as it did:

```vcl
sub vcl_deliver {
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
}
```

And apply:

```bash
kubectl apply -k .
```

```bash
kubectl get pods -l app=varnish
```

```bash
kubectl get replicaset -l app=varnish
```

`-l app=varnish` shows only what carries that label. **You should see** the old pod, same name, still counting its age, and the failed one on its way out:

```text
NAME                       READY   STATUS        RESTARTS      AGE
varnish-5fd5877cc7-sz2d6   0/1     Terminating   3 (17s ago)   52s
varnish-65464df99b-vn8zq   1/1     Running       0             65s
NAME                 DESIRED   CURRENT   READY   AGE
varnish-5fd5877cc7   0         0         0       52s
varnish-65464df99b   1         1         1       66s
varnish-7b8c6644dd   0         0         0       107s
```

**Why:**

- **Same lesson as Part 1 step 8, with a different gate.** A rollout waits for the new pod to be ready before it removes the old one. A VCL that doesn't compile never becomes ready, so the release stalls and the old pod, with its warm cache, keeps serving.
- **`kubectl get pods` says what and `kubectl logs` says why.** The status word `CrashLoopBackOff` means "it keeps dying and I am waiting longer between restarts". It's the first thing everyone sees and the least useful. The compiler's message is in the logs, with the line and the column.
- **Why the old pod is the same pod:** your fixed file hashes to the same ConfigMap name as before the typo, so the pod template is exactly the old one, and the Deployment brings back the old ReplicaSet instead of making a new one.

  The list shows it: three records, one for each VCL you applied, and the one with a pod is the second, not a new fourth. The pod that kept serving is never replaced. Your cache survived a failed release.
- **The typo is a Part 1 lesson again.** Python looks a name up only when its line runs; VCL looks every name up when it compiles, which is why the failure comes at once and in full.
- **Why a fixed-name ConfigMap would be worse:** the file inside a running pod changes about a minute after you edit the ConfigMap (62 seconds when it was timed), while Varnish keeps running the old VCL. A restart that night would then fail with nothing in the Deployment to say why. A hashed name means a pod's config can't change under it.

## 8. Write the load

A *Job* is a pod that runs once, to the end, and stays as a record. Ours asks Varnish for a fixed set of URLs at a fixed rate and counts hits from the `X-Cache` header. The script is the one place where the measurement is defined.

Type this as `load.py`:

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


def work(index, results):
    rng = random.Random(SEED + index)
    interval = WORKERS / RATE
    start = time.monotonic()
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
        results.append((outcome, time.monotonic() - began))


def main():
    results = [[] for _ in range(WORKERS)]
    threads = [
        threading.Thread(target=work, args=(i, results[i])) for i in range(WORKERS)
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
    counts = Counter(outcome for outcome, _ in done)
    times = sorted(taken for _, taken in done)
    total = len(done)
    print(f"requests={total} hit={counts['HIT']} miss={counts['MISS']}")
    print(f"other={total - counts['HIT'] - counts['MISS']}")
    print(f"hit_rate={counts['HIT'] / total:.3f}")
    print(f"p50_ms={times[total // 2] * 1000:.0f}")
    print(f"p95_ms={times[int(total * 0.95)] * 1000:.0f}")


if __name__ == "__main__":
    main()
```

Let the formatter and the linter read it, as in Part 1 step 2:

```bash
ruff format load.py
```

```bash
ruff check load.py
```

**You should see:** `1 file left unchanged` (or `1 file reformatted` if a space differs), then `All checks passed!`

Now put the script in a ConfigMap the way Part 1 step 5 did:

```bash
kubectl create configmap load-code --from-file=load.py --dry-run=client -o yaml > 06-load-code.yaml
```

```bash
kubectl apply -f 06-load-code.yaml
```

Type this as `07-load-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: load
  labels:
    app: load
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: load
    spec:
      restartPolicy: Never
      containers:
        - name: load
          image: python:3.13-alpine
          command: ["python", "/app/load.py"]
          env:
            - name: TARGET
              value: "http://varnish"
            - name: URLS
              value: "200"
            - name: RATE
              value: "20"
            - name: SECONDS
              value: "360"
            - name: WORKERS
              value: "8"
            - name: SEED
              value: "1"
          volumeMounts:
            - name: code
              mountPath: /app
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 32Mi
            limits:
              memory: 64Mi
      volumes:
        - name: code
          configMap:
            name: load-code
```

**Why, and every parameter of the measurement:**

- **200 different URLs**, `/p/0` to `/p/199`. The origin answers any path with a page (Part 1 step 2 said this would matter), so we can have as many as we like.
- **20 requests a second for 360 seconds, 7200 requests.** Each URL is asked for about every 10 seconds, so about 12 times in the origin's 120-second lifetime. The first of those is a miss and the rest are hits: about one miss in twelve.

  The numbers were chosen for Parts 3 and 4: with two caches that share the traffic, each URL has to warm in both, so the misses roughly double. That's large enough to see and small enough that most requests are still hits.
- **360 seconds is three lifetimes of a page.** A URL is cold when the run starts, fetched, expires after 120 seconds, is fetched again, and so on. The run starts with an empty cache and counts that warm-up on purpose: a cache that has just started is exactly what Part 4 kills.
- **8 workers, each one request every 0.4 seconds.** A miss takes 300 ms, shorter than 0.4, so a worker is never behind. The schedule is fixed in time and doesn't wait for answers.
- **Which URL comes next is random, with a fixed seed.** Each worker seeds its own generator from `SEED + its number`, so every run, now and in Parts 3 and 4, asks for the same URLs in the same order per worker. The timing between workers still differs a little.
- **The parameters live in the Job, not the script.** `os.environ[...]` with no default means the script refuses to run with one missing, so the numbers in your results are the numbers in this file.
- **The Job's `backoffLimit: 0`** means: if it fails, don't run it again. A rerun would send another 7200 requests into a cache you think is cold.
- **No probes on a Job.** It isn't a service anyone calls; its result is the exit and the logs.
- **The Job isn't in `kustomization.yaml`.** A Job's pod template can't be changed once it exists, so it's applied with `-f`, deleted, and applied again.

## 9. The first run

Write your guess down before you look. One cache, about one miss in twelve requests: what hit rate is that? **Don't skip this.** A measurement you didn't predict is one you can't tell is wrong.

The cache lives in the pod's memory and step 7 left it warm, so start from a cold one. This is the step people skip, and a second run on a warm cache gives a better number that means nothing:

```bash
kubectl rollout restart deployment/varnish
```

```bash
kubectl rollout status deployment/varnish
```

```bash
kubectl apply -f 07-load-job.yaml
```

The load runs for 360 seconds, plus the time its pod takes to start. Wait for it to finish:

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

**You should see:**

```text
job.batch/load condition met
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6600 miss=600
other=0
hit_rate=0.917
p50_ms=1
p95_ms=302
```

`other` counts errors and answers with no `X-Cache` header. It should be zero; if it's not, the run isn't a measurement, so look at why before you use it.

Now check the number against two other witnesses. First Varnish's own counters, which started at zero with the restart:

```bash
kubectl exec deploy/varnish -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

Then the origin's, which logs every request it serves. Every miss is exactly one request there:

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

**You should see** all three agree:

```text
MAIN.cache_hit            6600        17.93 Cache hits
MAIN.cache_miss            600         1.63 Cache misses
600
```

And 600 isn't luck. It's 200 pages, each fetched once per 120-second lifetime, over three lifetimes.

**Why:**

- **`kubectl rollout restart` is zero-downtime for one replica.** A Deployment of one copy starts the new pod before it stops the old one, so there's no moment with nothing answering. It's also the one command that gives you a cold cache without touching a file.
- **Three witnesses agree or the number isn't trusted.** The Job counts from the header, Varnish counts from its own counters, and the origin counts from the requests it actually served. If they differ, find out why before you write anything down.
- **`--tail=-1`** is needed because `kubectl logs` with a label shows only the last 10 lines of each pod unless told otherwise.
- **Varnish stops when it's asked to.** Deleting a Varnish pod took 0.76 seconds in the run behind this guide, against the origin's 30 before Part 1 step 9 fixed it. `varnishd` handles the stop signal itself.
- **`p50` and `p95` are the median and the 95th percentile of the latency the client saw.** A hit comes back in about a millisecond, and the slowest twelfth of the requests are the misses that waited 300 ms for the origin, which is why the two numbers sit so far apart. You don't use them yet. Part 4 asks whether a latency target holds, and it needs a value from before the failure to compare with.

## 10. The second run, and how much memory it used

Before you restart, look at how much of the memory limit the cache actually used. It's warm now, and the next restart throws it away.

```bash
kubectl exec deploy/varnish -- cat /sys/fs/cgroup/memory.current
```

```bash
kubectl exec deploy/varnish -- grep -E "^(anon|file|kernel) " /sys/fs/cgroup/memory.stat
```

```bash
kubectl exec deploy/varnish -- varnishstat -n /tmp/varnish -1 -f SMA.s0.g_bytes -f MAIN.n_object
```

```bash
kubectl exec deploy/varnish -- du -sh /tmp/varnish
```

**You should see:**

```text
104456192
anon 12308480
file 81633280
kernel 9945088
MAIN.n_object              200          .   object structs made
SMA.s0.g_bytes           74690          .   Bytes outstanding
81M	/tmp/varnish
```

The first number is what the kernel counts against the limit, in bytes: about 104 MB of the 256. The next three lines say what it's made of. `anon` is the process's own memory, about 12 MB, and the cache's 200 pages, 75 KB of them, are inside it.

`file` is 82 MB of pages that belong to files, and the last line shows which files: the working folder Varnish keeps under `-n`, its shared log among them. **Most of what the pod "uses" is neither the cache nor the process.**

File pages count against the limit, but the kernel can drop them when memory is tight. That was tested: a copy of this Deployment with a 64Mi limit took the same load at the same time. It gave the same 6600 hits and 600 misses and the same latency, it was never killed, and it sat at 63 MB with its file pages cut to 42 MB.

So the number to watch is `anon`. It's the part that can't be dropped, and it grows as the cache fills. A cache is killed when that part reaches the limit, and this lab's 200 pages, 74,690 bytes in all in the reading above (`SMA.s0.g_bytes`), never come near it.

**Two of these numbers depend on when you look.** Read them within two minutes of the load ending. Every page lives 120 seconds, and after that Varnish throws it away, so four minutes later `n_object` and `g_bytes` both read 0. That's the lifetime doing its job, and an empty cache telling you so.

The `file` figure moves as well: the folder holds 81 MB on disk, but only the part in use sits in memory, so yours may read 58 MB where this run read 82. `anon` is the steady one.

**If the pod was ever killed here,** `kubectl describe pod` shows `Last State: Terminated`, `Reason: OOMKilled`, and `Exit Code: 137` under the container. That would mean the limit is too low, and it's a finding to write down.

Now repeat the measurement from a cold cache, with the same file, so the only thing that differs between the two runs is time. A Job can't be started again, so delete it first:

```bash
kubectl delete job load
```

```bash
kubectl rollout restart deployment/varnish
```

```bash
kubectl rollout status deployment/varnish
```

```bash
kubectl apply -f 07-load-job.yaml
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
kubectl exec deploy/varnish -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

**You should see** the same lines as step 9. In the run behind this guide the second run matched the first to the request: `requests=7200 hit=6600 miss=600`, `hit_rate=0.917`, `p50_ms=1`, `p95_ms=302`, and the origin count read `1200`. The Varnish counters equal this run's `hit` and `miss`, because the restart zeroed them. The origin count is **cumulative**: it also holds the first run's fetches, so subtract the first run's `miss` to get this run's.

**Why:**

- **Two runs show the number repeats, or that it does not.** One run is a number; two are the start of a measurement. Compare the two `hit_rate` lines. If they differ by more than you would trust, run it a third time and write all three down, rather than keeping the one you like.
- **What can differ between runs:** the order in which different workers' requests reach the cache, and a request that arrives while the same page is being fetched. What cannot: the URLs, the rate, the length, the lifetime.
- **`delete job` first** because the name `load` is taken, and a finished Job is a record, not something to restart.

## 11. Write the number down

The numbers are the product of this part. Type this as `results.md`, with your own values in place of the dots:

```markdown
# Results

| Part | Setup | URLs | Rate | Seconds | Seed | Run | Hit rate | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 1 | ... | ... | ... |
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 2 | ... | ... | ... |
```

**Why:** Parts 3 and 4 add rows to this table, and the comparison only means something if the first rows say exactly what was run. For comparison, the run behind this guide wrote 0.917, 1 and 302 on both rows.

This repository's own [results.md](../../results.md) starts with the same table, without the heading, and then holds every run measured on the finished files.

## 12. Commit

```bash
git add kustomization.yaml default.vcl 04-varnish.yaml 05-varnish-service.yaml load.py 06-load-code.yaml 07-load-job.yaml results.md
```

```bash
git commit -m "One Varnish in front of the origin, its VCL through Kustomize, and a measured hit rate"
```

The deletion of `01-origin-code.yaml` from step 1 is already staged and goes in the same commit. Leave the cluster as it is for Part 3. If you delete it instead, the folder brings Part 2 back:

```bash
kind create cluster --name lab --config kind-cluster.yaml
```

```bash
kubectl apply -f 00-namespace.yaml
```

```bash
kubectl config set-context --current --namespace=lab
```

```bash
kubectl apply -k .
```

```bash
kubectl apply -f 06-load-code.yaml
```

That was run on an emptied namespace, and both ConfigMaps came back under the same hashed names.

## What this doesn't cover

Two caches, which is Part 3; what the number would be for real traffic, which isn't this traffic; sizing a cache for production; or what happens to a cache that's restarted under load, which is Part 4. The number you measured belongs to this load on this laptop.

**Next:** [Part 3: two Varnish, and the problem](3-two-varnish-and-the-problem.md)
