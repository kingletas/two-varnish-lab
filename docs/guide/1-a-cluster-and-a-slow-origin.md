# Part 1: a cluster and a slow origin

**What you have at the end:** three nodes, two copies of a small backend, one stable name in front of them, and three things you broke on purpose and fixed.

## 1. Make the folder

```bash
mkdir two-varnish-lab && cd $_
```

```bash
git init
```

**Why:** everything the cluster runs will come from files in this folder. The cluster is disposable; the folder is the lab.

## 2. Write the origin, and run it on your laptop first

The *origin* is the backend a cache sits in front of. Ours is slow on purpose, 300 ms a page, so a cache hit is easy to see later. Every page says which copy made it and when.

Type this as `server.py`:

```python
"""A slow origin: every page takes 300 ms and says which pod made it."""

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POD = os.environ.get("POD_NAME", "local")
DELAY = float(os.environ.get("ORIGIN_DELAY", "0.3"))


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.reply("ok\n", cacheable=False)
            return
        time.sleep(DELAY)
        made = time.strftime("%H:%M:%S")
        self.reply(f"path={self.path} pod={POD} made={made}\n", cacheable=True)

    def reply(self, body, cacheable):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control", "public, s-maxage=120" if cacheable else "no-store"
        )
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"{POD} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Origin).serve_forever()
```

Before you run it, let the formatter fix the spacing and a linter read it:

```bash
ruff format server.py
```

```bash
ruff check server.py
```

**You should see:** `1 file left unchanged` if you typed it exactly as above, or `1 file reformatted` if not, and then `All checks passed!`

**Why first:** Python looks a name up only when its line runs, so a typo on a line your test never reaches stays hidden. A linter reads every line without running any. `python3 -m py_compile` isn't enough: it checks that the file is valid Python and passes a misspelled name.

Run it in one terminal:

```bash
python3 server.py
```

And ask it for a page from another:

```bash
curl -si localhost:8080/shoes
```

**You should see:**

```text
HTTP/1.0 200 OK
Content-Type: text/plain; charset=utf-8
Content-Length: 36
Cache-Control: public, s-maxage=120

path=/shoes pod=local made=11:19:53
```

Ask for the health page too. The cluster will call this one every few seconds, so prove it works here first:

```bash
curl -si localhost:8080/healthz
```

**You should see** `200 OK`, `Cache-Control: no-store`, and the body `ok`. If the server terminal prints a Python traceback instead, fix it now: on the cluster the same bug shows up as a pod that restarts forever.

Stop the server with Ctrl-C.

**Why this way:**

- **Run it before you containerize it.** If it fails on the cluster later, you already know the code isn't the reason.
- **`s-maxage=120`** tells a shared cache it may keep the page for two minutes. That header is what Varnish will obey in Part 2.
- **`/healthz` answers at once and is never cached.** The cluster will ask it "are you alive?" every few seconds, and that question must not wait 300 ms or get a stale answer.
- **Any other path is a page.** `/shoes`, `/a`, `/anything` all return 200. That gives us as many distinct URLs as we want for the cache test. Remember it: it matters in step 8.

## 3. Describe the cluster, then create it

Type this as `kind-cluster.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
```

```bash
kind create cluster --name lab --config kind-cluster.yaml
```

```bash
kubectl get nodes
```

**You should see**, after about a minute:

```text
NAME                STATUS   ROLES           AGE   VERSION
lab-control-plane   Ready    control-plane   45s   v1.36.1
lab-worker          Ready    <none>          33s   v1.36.1
lab-worker2         Ready    <none>          32s   v1.36.1
```

The workers say `NotReady` for the first few seconds. Run the command again.

**Why:**

- **A node is one machine.** Here each node is a Docker container pretending to be a machine, which is what kind does.
- **The control plane** is the node that holds the cluster's state and makes the decisions. **Workers** run your containers.
- **Two workers, not one,** because the whole lab is about what happens when a copy on one machine dies. With one worker there's nothing to lose.
- **`kind create cluster` also points `kubectl` at the new cluster.** It writes a *context* named `kind-lab` into your kubeconfig and makes it current. `kubectl config current-context` shows it.

## 4. Give the lab its own namespace

Type this as `00-namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: lab
```

```bash
kubectl apply -f 00-namespace.yaml
```

```bash
kubectl config set-context --current --namespace=lab
```

**You should see:** `namespace/lab created`, then `Context "kind-lab" modified.`

**Why:** a *namespace* is a named room inside the cluster. Everything we make goes in `lab`, so `kubectl get pods` shows our things and not the cluster's own. The second command makes `lab` the default room for every command you type from here on.

## 5. Put the code where the cluster can reach it

We aren't building an image yet. A *ConfigMap* holds small files and settings, and a pod can mount it as a folder. Let `kubectl` write the manifest from your file, then read what it wrote.

```bash
kubectl create configmap origin-code --from-file=server.py --dry-run=client -o yaml > 01-origin-code.yaml
```

Open `01-origin-code.yaml`. **You should see** your Python under `data:`, with `server.py: |` above it.

```bash
kubectl apply -f 01-origin-code.yaml
```

**Why:**

- **`--dry-run=client -o yaml` means "print it, don't create it".** You get a file you can read and commit, and the cluster only ever sees what's in the folder.
- **You didn't type this one by hand** because Python inside YAML is an indentation trap, and the lesson here is the ConfigMap, not the spacing.
- **A ConfigMap is for small text.** Real application code ships in an image. For a 30-line lab server it keeps the loop short: edit, regenerate, apply.

## 6. The Deployment, typed by hand

This is the file to type slowly. Every block is a decision.

Type this as `02-origin.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: origin
  labels:
    app: origin
spec:
  replicas: 2
  selector:
    matchLabels:
      app: origin
  template:
    metadata:
      labels:
        app: origin
    spec:
      containers:
        - name: origin
          image: python:3.13-alpine
          command: ["python", "/app/server.py"]
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
          volumeMounts:
            - name: code
              mountPath: /app
              readOnly: true
      volumes:
        - name: code
          configMap:
            name: origin-code
```

```bash
kubectl apply -f 02-origin.yaml
```

```bash
kubectl rollout status deployment/origin
```

```bash
kubectl get pods -o wide
```

**You should see:** `deployment "origin" successfully rolled out`, then two pods named `origin-…`, both `Running`, **usually one on each worker**. The scheduler prefers to spread a Deployment's pods and doesn't promise it. If both of yours are on one worker, step 10's lesson has arrived early: carry on, and it's fixed properly in Part 4.

**Why, block by block:**

- **`replicas: 2`** is the desired state: two copies, always. You never start a container. You state how many should exist and a controller keeps it true.
- **`selector` and the template's `labels` must match.** The Deployment finds its pods by label, not by name. If they differ, it creates pods forever and owns none of them.
- **A *pod*** is the smallest thing the cluster runs: one or more containers that live and die together. Here, one.
- **`ports` with `name: http`** names the port once. The Service and the probes will say `http` and never repeat `8080`.
- **`fieldRef: metadata.name`** hands the pod its own name as `POD_NAME`. This is the *Downward API*, and it's why each page can say which copy made it.
- **`volumes` and `volumeMounts`** are two halves: the first says "there's a folder that comes from the ConfigMap", the second says "show it to this container at `/app`".
- **The image tag is pinned** to `3.13-alpine`. With `latest` you can't tell what's running or go back to it.

## 7. One stable name in front

Pods come and go, and each new one gets a new address. A *Service* is a fixed name in front of whichever pods match its selector.

Type this as `03-origin-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: origin
  labels:
    app: origin
spec:
  selector:
    app: origin
  ports:
    - name: http
      port: 80
      targetPort: http
```

```bash
kubectl apply -f 03-origin-service.yaml
```

Now ask it for the same page six times, from inside the cluster:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2 3 4 5 6; do wget -qO- http://origin/shoes; done'
```

**You should see** both pod names, mixed:

```text
path=/shoes pod=origin-58cdcbdcbb-bsrqt made=15:21:29
path=/shoes pod=origin-58cdcbdcbb-bsrqt made=15:21:29
path=/shoes pod=origin-58cdcbdcbb-6m55p made=15:21:30
path=/shoes pod=origin-58cdcbdcbb-6m55p made=15:21:30
path=/shoes pod=origin-58cdcbdcbb-bsrqt made=15:21:30
path=/shoes pod=origin-58cdcbdcbb-6m55p made=15:21:31
```

**Why:**

- **`http://origin` works because the cluster has its own DNS.** Every Service gets a name; inside the namespace the short name is enough.
- **The Service listens on 80 and forwards to the port named `http`.** The pod's 8080 appears in exactly one file.
- **The same URL landed on both copies.** A Service spreads connections; it doesn't know or care what the URL is. **Hold on to that.** It's harmless for an origin and it's the whole problem once the copies are caches: the page warmed on one is cold on the other.
- **`kubectl run … --rm`** makes a throwaway pod that deletes itself. We test from inside because that's where Varnish will call from.

## 8. Tell the cluster what "healthy" means, then break it

Add this to `02-origin.yaml`, inside the container, right after the `volumeMounts` block and at the same indentation as `volumeMounts`:

```yaml
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
              cpu: 50m
              memory: 32Mi
            limits:
              memory: 64Mi
```

```bash
kubectl apply -f 02-origin.yaml
```

```bash
kubectl rollout status deployment/origin
```

**You should see:** `successfully rolled out`, and in `kubectl get pods` two new pods replacing the two old ones.

**Why:**

- **Readiness** answers "may I send you traffic?". A pod that fails it stays running and is taken out of the Service.
- **Liveness** answers "are you stuck?". When a pod fails it three times in a row, its container is restarted inside the same pod: the pod keeps its name, and the `RESTARTS` column goes up by one.
- **Liveness is the patient one, on purpose.** Readiness gives up after 15 seconds here (every 5 seconds, three failures, the default) and liveness after 30 (every 10, three failures). A failed readiness check costs nothing, because the pod comes back into the Service by itself. A failed liveness check kills a process. So liveness waits longer, and it never asks about anything outside its own process.
- **They are different questions**, even when they ask the same URL. Readiness protects your users; liveness protects you from a hung process.
- **`requests`** is what the scheduler reserves for the pod when it picks a node: a twentieth of a core and 32 MiB.
- **`limits.memory`** is a hard ceiling: past 64 MiB the container is killed. **There's no CPU limit on purpose.** A CPU limit throttles a container even when the node is idle, so the usual advice is a CPU request and no CPU limit.

**Now break it.** In `02-origin.yaml`, change the readiness probe's port from `http` to `9999`, so the readiness probe reads:

```yaml
          readinessProbe:
            httpGet:
              path: /healthz
              port: 9999
            periodSeconds: 5
```

Leave the liveness probe as it is, and apply:

```bash
kubectl apply -f 02-origin.yaml
```

```bash
kubectl rollout status deployment/origin --timeout=30s
```

```bash
kubectl get pods
```

**You should see** the rollout give up waiting, the two old pods still `1/1`, and one new pod stuck at `0/1`:

```text
Waiting for deployment "origin" rollout to finish: 1 out of 2 new replicas have been updated...
error: timed out waiting for the condition
NAME                      READY   STATUS    RESTARTS   AGE
origin-5fd788cdcf-5lp8g   1/1     Running   0          38s
origin-5fd788cdcf-rx5wg   1/1     Running   0          32s
origin-9745c64fd-jwl9k    0/1     Running   0          30s
```

Ask for the same page six times again, from inside the cluster:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2 3 4 5 6; do wget -qO- http://origin/shoes; done'
```

**It still answers**, from the two old pods.

**This is the lesson of the step:** a rollout replaces pods one at a time and waits for each new one to be *ready* before it removes an old one. A bad release stalls; it doesn't take the service down. This is the cluster's version of a release that won't finish until the health check passes.

**Fix it the right way.** Put `port: http` back in the file, so the readiness probe reads as it did:

```yaml
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
```

And apply it again:

```bash
kubectl apply -f 02-origin.yaml
```

**Why not `kubectl rollout undo`:** it works, and kubectl itself warns you about it. The cluster would go back, and your file would still say `9999`. **The file is the truth.** Fix the file and apply it, so the folder and the cluster never disagree.

> **What the first draft of this step got wrong.** The first version broke the probe by changing the *path* to `/nope`. On the cluster the rollout succeeded anyway. **Why:** this origin answers 200 for every path, which is what step 2 set up on purpose. A health check that can never fail isn't a health check. The port is what makes it fail here. In a real application, make sure the health path is one that can say no.

## 9. Why does a pod take thirty seconds to die?

Delete one pod and time it. Use a name from your own `kubectl get pods`:

```bash
time kubectl delete pod origin-5fd788cdcf-5lp8g
```

**You should see** it take about 30 seconds. It was 30.49 here.

**What broke:** nothing looks broken, and that's the trap. The pod is deleted and a new one appears. It just takes half a minute.

**Why:** when the cluster stops a pod it sends the process a polite signal, `SIGTERM`, waits up to 30 seconds (the *termination grace period*), and then kills it with `SIGKILL`. Our Python server is the first process in its container, process number 1, and process 1 gets no default signal handlers. It ignores the polite signal. So every stop waits out the full 30 seconds and ends in a kill.

**Why it matters for this lab:** Part 4 kills a Varnish under load and measures recovery. If our own pieces take 30 seconds to notice they were asked to stop, every number we measure has that wait hidden inside it.

**The fix.** In `server.py`, add `import signal` to the imports, between `os` and `time`, so they read:

```python
import os
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
```

Then replace the last two lines of the file with:

```python
def stop(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    ThreadingHTTPServer(("0.0.0.0", 8080), Origin).serve_forever()
```

Your `server.py` is now the same file as the repository's. Lint it again before it goes anywhere:

```bash
ruff format server.py
```

```bash
ruff check server.py
```

Regenerate the ConfigMap and apply it:

```bash
kubectl create configmap origin-code --from-file=server.py --dry-run=client -o yaml > 01-origin-code.yaml
```

```bash
kubectl apply -f 01-origin-code.yaml
```

```bash
kubectl get pods
```

**You should see:** `configmap/origin-code configured`, and **the same two pods, same age.** Nothing restarted.

**Second lesson, and it catches everyone:** changing a ConfigMap doesn't restart the pods that use it. The Deployment didn't change, so the controller sees nothing to do. The pods are still running the old code. Tell it to roll:

```bash
kubectl rollout restart deployment/origin
```

```bash
kubectl rollout status deployment/origin
```

Now time a delete again, **on one of the new pods**:

```bash
time kubectl delete pod origin-d48794478-54ctb
```

**You should see** about one second. It was 1.02 here.

**Why this fix and not a shorter grace period:** lowering the 30 seconds only makes the kill come sooner, and the process still ignores the request. **What you have now is a server that stops when it's asked. That's half of what the term *graceful shutdown* means,** and a reviewer looks for both halves in anything that runs on a cluster.

This server exits at once, so a page it was in the middle of sending is cut off, and for a moment the Service can still send it a request it will never answer. Finishing what's in flight is the other half. Part 4 teaches it, because that's where the load can show the difference.

Part 2 removes the manual restart: Kustomize gives each ConfigMap version its own name, so a config change rolls the pods by itself.

## 10. Take a machine away

Look at where your pods are, then empty one worker:

```bash
kubectl get pods -o wide
```

```bash
kubectl drain lab-worker --ignore-daemonsets --delete-emptydir-data
```

```bash
kubectl get pods -o wide
```

`--delete-emptydir-data` has nothing to do yet: no pod here has an `emptyDir` volume. The section after Part 3 adds one and shows the drain that refuses without the flag.

**You should see** the pod on `lab-worker` evicted, a replacement started, and **both pods now on `lab-worker2`.** `kubectl get nodes` shows `lab-worker` as `Ready,SchedulingDisabled`.

Bring the worker back:

```bash
kubectl uncordon lab-worker
```

```bash
kubectl get pods -o wide
```

**You should see:** the node is `Ready` again, and **both pods are still on `lab-worker2`.**

**Why:**

- **`drain`** is how you take a machine out for maintenance: it marks the node unschedulable (*cordon*) and evicts its pods, and the Deployment recreates them elsewhere. It's the polite version of a machine dying.
- **`--ignore-daemonsets`** is needed because every node runs a few system pods that belong to the node itself. Drain leaves those alone.
- **Nothing moves back.** The cluster places a pod when it's created and then leaves it. So after one ordinary maintenance, both copies of your service sit on one machine, and losing that machine loses both.

**Leave it like that and write the question down.** Part 4 answers it with two tools: a rule that spreads copies across nodes, and a PodDisruptionBudget that stops a drain from taking the last one.

## 11. Commit

```bash
git add server.py kind-cluster.yaml 00-namespace.yaml 01-origin-code.yaml 02-origin.yaml 03-origin-service.yaml
```

```bash
git commit -m "A three-node cluster and a slow origin with probes and a clean stop"
```

When you're done for the day, the cluster goes away in one command and comes back from the folder:

```bash
kind delete cluster --name lab
```

And to bring it back before Part 2:

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
kubectl apply -f 01-origin-code.yaml -f 02-origin.yaml -f 03-origin-service.yaml
```

## What you can now say, and what you can't yet

| You can say | Because you did it |
|---|---|
| I have run a multi-node Kubernetes cluster locally and deployed to it from manifests I wrote | Steps 3 to 7 |
| I know what readiness and liveness probes do to a rollout, and I have watched a bad release stall without an outage | Step 8 |
| I have fixed a container that ignored SIGTERM, and measured the difference | Step 9 |
| I have drained a node and seen where the pods went | Step 10 |

**Not yet:** anything about Kubernetes in production, Ingress, Helm, autoscaling, or a managed service such as EKS. A local lab is a local lab, and the page says so.

**Next:** [Part 2: one Varnish in front](2-one-varnish-in-front.md)
