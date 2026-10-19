# Between Parts 3 and 4: tighten what you typed

This section reviews Parts 1 to 3: what to tighten, and why. Each step below changes a file. None of them changes what you measured, and step 7 measures again to check.

**What you have at the end:** probes with nothing left to a default, a namespace the folder owns, a cache whose memory is all accounted for, a load whose script can't drift from what ran, and a lab that comes back from the folder in two commands.

## 1. Write the probes' timeout out

In `02-origin.yaml`, `04-varnish.yaml` and `09-router.yaml`, add one line under each `periodSeconds`, at the same indentation. Six lines in all:

```yaml
            timeoutSeconds: 1
```

In `02-origin.yaml` the two probes now read:

```yaml
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
            timeoutSeconds: 1
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            timeoutSeconds: 1
            failureThreshold: 3
```

In `04-varnish.yaml` they're the same lines, for now:

```yaml
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
            timeoutSeconds: 1
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            timeoutSeconds: 1
            failureThreshold: 3
```

And in `09-router.yaml`:

```yaml
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
```

```bash
kubectl apply -k .
```

```bash
kubectl get pods
```

**You should see** `configured` on the two Deployments and the StatefulSet, and then the same pods as before: same names, ages still counting.

**Why:**

- **One second is the default, and now the file says so.** It's the same reason Part 2 switched grace off. A probe that waits one second is a decision about how slow "too slow" is, and Part 4 puts these pods under load.
- **Nothing restarted, because you typed what was already true.** The cluster had filled that value in by itself. The file changed; what the pods run under did not.

## 2. Put the namespace in the folder

Until now `lab` lived in your terminal: `kubectl config set-context` in Part 1 step 4. A manifest applied from a terminal that was never told would land in `default`. In `kustomization.yaml`, two lines are new: `namespace: lab` goes between `kind` and `resources`, and `- 00-namespace.yaml` goes at the top of the `resources` list, above `02-origin.yaml`. Nothing else in the file changes. The whole file now reads:

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

```bash
kubectl apply -k .
```

**You should see** `namespace/lab unchanged` as a new first line, and `unchanged` on everything under it.

**Why:** "the folder is the truth" now holds for the namespace too. Keep the context setting in your terminal: it's still what lets you type `kubectl get pods` without `-n lab`. It's a convenience now, not something the folder depends on.

## 3. Find out what the 81 MB is

Part 2 step 10 found 81 MB in Varnish's working folder and reasoned about what it was. Look:

```bash
kubectl exec varnish-0 -- sh -c 'du -a /tmp/varnish | sort -n | tail -5'
```

**You should see**, with your own letters and digits in the names:

```text
96	/tmp/varnish/vcl_boot.1773326294.342243/vgc.so
100	/tmp/varnish/vcl_boot.1773326294.342243
81924	/tmp/varnish/_.vsm_child/_.Log.d55843da1e0e4823
82132	/tmp/varnish/_.vsm_child
82348	/tmp/varnish
```

Sizes are in KB. **One file, `_.Log`, is 80 MB of the 81.**

**Why:**

- **That file is Varnish's shared log.** Every request is written into it, in a ring of fixed size, and `varnishlog` reads from it. Its size is a Varnish parameter, `vsl_space`, which Varnish's documentation gives as 80 MB by default; the file here is 83,886,080 bytes, which is 80 MiB exactly.
- **`vgc.so` is your VCL.** Varnish turns `default.vcl` into C, compiles it, and loads the result from this folder. Remember that for step 6: whatever this folder is mounted on has to allow running a file from it.

## 4. Drain a worker, and notice what the command didn't need

```bash
kubectl get pods -o wide
```

```bash
kubectl drain lab-worker --ignore-daemonsets
```

```bash
kubectl get pods -o wide
```

```bash
kubectl uncordon lab-worker
```

**You should see** the drain finish, without the `--delete-emptydir-data` you typed in Part 1 step 10. In the run behind this guide:

```text
node/lab-worker cordoned
Warning: ignoring DaemonSet-managed Pods: kube-system/kindnet-5thtt, kube-system/kube-proxy-7thq7
evicting pod lab/varnish-0
evicting pod lab/origin-75b8688f98-swvqt
evicting pod lab/origin-75b8688f98-8fsr2
evicting pod lab/router-57c99c7447-jr26k
evicting pod lab/router-57c99c7447-lcn4t
pod/router-57c99c7447-jr26k evicted
pod/origin-75b8688f98-8fsr2 evicted
pod/router-57c99c7447-lcn4t evicted
pod/varnish-0 evicted
pod/origin-75b8688f98-swvqt evicted
node/lab-worker drained
```

**Why:**

- **The flag had nothing to do.** It gives permission to evict a pod that has an `emptyDir`, a scratch folder that's deleted with the pod. No pod of yours has one yet. Step 6 adds one, and step 8 drains again.
- **Read which pods went.** In that run five of the six pods happened to be on `lab-worker`: both origins and both routers. One second after the drain, `kubectl get pods` showed all four as `ContainerCreating`. For that moment the lab had no origin and no router. Yours are probably spread differently, and that's the point: nothing in the folder says where they must be, or how many may go at once. Part 4 says both.

Before going on, wait until everything is `Running`:

```bash
kubectl get pods
```

And until the routers' status page says `UP` four times, once for each cache on each router:

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

## 5. The cache's memory, its second store, and a patient liveness probe

Three changes in `04-varnish.yaml`. Two more lines at the end of `args`:

```yaml
            - "-s"
            - "malloc,64m"
            - "-s"
            - "Transient=malloc,32m"
```

In the liveness probe, `failureThreshold: 3` becomes `failureThreshold: 6`, so it reads:

```yaml
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            timeoutSeconds: 1
            failureThreshold: 6
```

And under `requests`, `memory: 128Mi` becomes `memory: 256Mi`, the same as the limit, so `resources` reads:

```yaml
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              memory: 256Mi
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
kubectl exec varnish-0 -- varnishstat -n /tmp/varnish -1 -f SMA.s0.g_space -f SMA.Transient.g_space -f SMA.Transient.g_bytes
```

```bash
kubectl get pod varnish-0 -o jsonpath='{.status.qosClass}{"\n"}{.spec.containers[0].resources}{"\n"}'
```

**You should see:**

```text
SMA.s0.g_space        67108864          .   Bytes available
SMA.Transient.g_bytes            0          .   Bytes outstanding
SMA.Transient.g_space     33554432          .   Bytes available
Burstable
{"limits":{"memory":"256Mi"},"requests":{"cpu":"100m","memory":"256Mi"}}
```

**Why:**

- **`malloc,64m` only ever covered the main store.** Short-lived objects go to a second store, named `Transient`, and that one has no ceiling unless you give it one. 33,554,432 bytes is the 32 MiB you just typed.
- **The ceiling has a price.** When that store is full, a request that needs it's answered with an error, a 503. Here nothing uses it (`g_bytes` reads 0), which is why 32 MB is safe in the lab. On a real store it's a number to size from what the store uses, not one to copy. That's Varnish's documented behaviour; the lab doesn't fill the store to show it.
- **Request equal to limit, for the cache and only for the cache.** When a node runs short of memory, Kubernetes evicts first the pods that are using more than they requested. That's its documented order; this lab doesn't show it. A cache that requests everything it may use is never in that group, and for a cache, being evicted means starting cold.
- **`Burstable`, still.** The class would be `Guaranteed` only with a CPU limit equal to the CPU request, and Part 1 step 8 left the CPU limit out on purpose. So the pod isn't `Guaranteed`; only its memory request equals its limit.
- **Liveness now waits 60 seconds, not 30.** A restart is the most expensive thing that can happen to a cache: it comes back empty. A busy Varnish may answer its probe late, and a restart at that moment empties the cache exactly when it's needed. The router's liveness was already at 6; the origin's stays at 3, because an origin holds nothing.

Now take one reading to compare with after the next step:

```bash
for p in $(kubectl get pods -l app=varnish -o name); do echo $p; kubectl exec $p -- sh -c "cat /sys/fs/cgroup/memory.current; grep -E \"^(anon|file|shmem) \" /sys/fs/cgroup/memory.stat"; done
```

**You should see** something like:

```text
pod/varnish-0
100749312
anon 6053888
file 84303872
shmem 0
pod/varnish-1
99368960
anon 5955584
file 83136512
shmem 0
```

About 100 MB in use, and 84 of it is `file`: the kernel's copy of that log file. `shmem 0`.

## 6. Put Varnish's working folder in memory, with a size

That log is written on every request, and so far it sits on the container's disk. The usual advice is to keep this folder in memory. Add a second mount and a second volume to `04-varnish.yaml`. The container's `volumeMounts` now reads:

```yaml
          volumeMounts:
            - name: vcl
              mountPath: /vcl
              readOnly: true
            - name: workdir
              mountPath: /tmp/varnish
```

And the pod's `volumes`, at the end of the file:

```yaml
      volumes:
        - name: vcl
          configMap:
            name: varnish-vcl
        - name: workdir
          emptyDir:
            medium: Memory
            sizeLimit: 128Mi
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
kubectl exec varnish-0 -- sh -c 'grep " /tmp/varnish " /proc/mounts; df -h /tmp/varnish | tail -1; ls -la /tmp/varnish'
```

**You should see:**

```text
tmpfs /tmp/varnish tmpfs rw,relatime,size=131072k,inode64,noswap 0 0
tmpfs           128M   81M   48M  63% /tmp/varnish
total 12
drwxrwxrwt 7 root    root     180 Mar 12 14:39 .
drwxrwxrwt 1 root    root    4096 Mar 12 14:39 ..
-rw-r--r-- 1 varnish varnish    1 Mar 12 14:39 _.pid
-rw-r----- 1 varnish varnish  256 Mar 12 14:39 _.secret
drwxr-x--- 2 varnish varnish  880 Mar 12 14:39 _.vsm_child
drwxr-xr-x 2 varnish varnish  200 Mar 12 14:39 _.vsm_mgt
drwxr-xr-x 2 varnish varnish   60 Mar 12 14:39 vcl_boot.1773326353.841336
drwxr-xr-x 2 varnish varnish   40 Mar 12 14:39 vmod_cache
drwxr-xr-x 2 varnish varnish   40 Mar 12 14:39 worker_tmpdir
```

Take the same memory reading as in step 5:

```bash
for p in $(kubectl get pods -l app=varnish -o name); do echo $p; kubectl exec $p -- sh -c "cat /sys/fs/cgroup/memory.current; grep -E \"^(anon|file|shmem) \" /sys/fs/cgroup/memory.stat"; done
```

**You should see** something like:

```text
pod/varnish-0
98353152
anon 6062080
file 84303872
shmem 84295680
pod/varnish-1
98226176
anon 6053888
file 84320256
shmem 84295680
```

**The total barely moved, and one line changed completely: `shmem` went from 0 to 84 MB.**

**Why:**

- **The same 84 MB, and a different kind of memory.** Before, it was the kernel's copy of a file on disk, which the kernel may drop when memory is short and read back later. Now it's the only copy. It can't be dropped, and it counts against the pod's limit for as long as the pod lives.
- **So the limit has to include it, and it does:** in the sample run behind this guide each cache used a little over 100 MB after a full load, against 256Mi. That figure wasn't written down. This is also why Part 2's small experiment, the copy that survived at 64Mi, shouldn't survive now. That wasn't tried again.
- **`sizeLimit: 128Mi` is a ceiling on the folder,** not a reservation. `df` shows 128M with 81M used.
- **`rw`, and no `noexec` on the mount line.** Varnish compiled your VCL into this folder and loaded it from there, which is why the pod came up at all. A mount that forbade running files would have stopped it at start.
- **The pod now has an `emptyDir`.** Hold that for step 8.

## 7. Give the load its own folder, then measure once

In Part 2, `06-load-code.yaml` was a ConfigMap made by hand from `load.py`. That's the kind of copy step 1 of Part 2 removed for the origin, and the load script is the last file that should be able to differ from what ran: it's where the measurement is defined.

```bash
mkdir load
```

```bash
git mv load.py load/load.py
```

```bash
git mv 07-load-job.yaml load/job.yaml
```

```bash
git rm 06-load-code.yaml
```

Type this as `load/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: lab
resources:
  - job.yaml
configMapGenerator:
  - name: load-code
    files:
      - load.py
```

Remove the hand-made ConfigMap from the cluster:

```bash
kubectl delete configmap load-code
```

The measuring cycle is the one from Part 3 step 9 with one line different: `kubectl apply -k load/` where it said `kubectl apply -f 07-load-job.yaml`. As there, repeat the status command until all four server lines say `UP` before you start the load.

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
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

```bash
kubectl apply -k load/
```

```bash
kubectl get configmap
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
for p in $(kubectl get pods -l app=varnish -o name); do echo $p; kubectl exec $p -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss; done
```

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

**You should see**, with your own origin counts (in this run the origin pods were new, so the first count was 0):

```text
0
configmap/load-code-6k54c2mt55 created
job.batch/load created
NAME                       DATA   AGE
kube-root-ca.crt           1      94s
load-code-6k54c2mt55       1      0s
origin-code-hb96279hb7     1      94s
router-config-hgt989dh28   1      94s
varnish-vcl-55hh5tc65c     1      94s
job.batch/load condition met
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6600 miss=600
other=0
hit_rate=0.917
p50_ms=1
p95_ms=302
pod/varnish-0
MAIN.cache_hit            2733         7.45 Cache hits
MAIN.cache_miss            249         0.68 Cache misses
pod/varnish-1
MAIN.cache_hit            3867        10.48 Cache hits
MAIN.cache_miss            351         0.95 Cache misses
600
```

**600 misses, 0.917, and the same 249 and 351 as Part 3 step 9.** Every step of this section came between that run and this one, and the measurement didn't move by one request.

**Why:**

- **`load-code-6k54c2mt55`.** The name now carries a fingerprint of `load.py`. If your file is byte for byte the guide's, yours ends the same way. Change one character of the script and the name changes, so a row in `results.md` can say which script produced it. Write the name beside this row.
- **A Job can't be edited once it exists,** which is why the cycle still starts by deleting the old one.
- **Kustomize doesn't clean up after itself here.** Each new version of `load.py` leaves the old ConfigMap behind. `kubectl get configmap` shows them, and deleting the stale ones is yours to do.

### The plain-Service load, in a folder of its own

Part 3 step 4 sent the same load to the plain `varnish` Service. Your `load/job.yaml` now says `http://router`, so that comparison can't be run again without editing the file. Give it a folder of its own instead, which changes that one value and nothing else:

```bash
mkdir load-via-service
```

Type this as `load-via-service/kustomization.yaml`:

```yaml
# The same load sent to the plain varnish Service instead of the router, which
# spreads requests across both caches without regard to the URL. Run it with
# `kubectl apply -k load-via-service` to measure what routing by URL is worth.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../load
patches:
  - target:
      kind: Job
      name: load
    patch: |-
      - op: test
        path: /spec/template/spec/containers/0/env/0/name
        value: TARGET
      - op: replace
        path: /spec/template/spec/containers/0/env/0/value
        value: http://varnish
```

Read what it would send, without sending it:

```bash
kubectl kustomize load-via-service | grep -A1 'name: TARGET'
```

**You should see** two lines: `- name: TARGET`, and under it `value: http://varnish`. Your `load/job.yaml` still says `http://router`.

**Why:**

- **`- ../load` takes the whole load folder**, its Job, its script and its namespace, and the patch changes the Job on the way out. There's still one copy of the script and one copy of the Job.
- **The patch finds the value by position:** the first container, its first `env` entry. The `test` line checks that entry is still named `TARGET` before the `replace` line runs. If the `env` list is ever reordered, the build fails, instead of quietly putting a URL into `URLS`.
- **The Job is still called `load`,** so a finished one has to be deleted first, whichever folder made it.

When you want that comparison again, this is the whole cycle, with `kubectl apply -k load-via-service` where the router's cycle has `kubectl apply -k load/`:

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
kubectl apply -k load-via-service
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

## 8. Drain again, now that a pod has an emptyDir

Find the worker `varnish-1` is on (`lab-worker` in this run), and drain that one, the way you did in step 4:

```bash
kubectl get pods -o wide
```

```bash
kubectl drain lab-worker --ignore-daemonsets
```

**You should see:**

```text
node/lab-worker cordoned
error: unable to drain node "lab-worker" due to error: cannot delete Pods with local storage (use --delete-emptydir-data to override): lab/varnish-1, continuing command...
There are pending nodes to be drained:
 lab-worker
cannot delete Pods with local storage (use --delete-emptydir-data to override): lab/varnish-1
```

```bash
kubectl get nodes
```

```text
NAME                STATUS                     ROLES           AGE     VERSION
lab-control-plane   Ready                      control-plane   8m      v1.36.1
lab-worker          Ready,SchedulingDisabled   <none>          7m45s   v1.36.1
lab-worker2         Ready                      <none>          7m45s   v1.36.1
```

Now with the flag, and then give the worker back:

```bash
kubectl drain lab-worker --ignore-daemonsets --delete-emptydir-data
```

```bash
kubectl uncordon lab-worker
```

**Why:**

- **This is the flag from Part 1, with its reason at last.** The drain won't delete a folder's contents without being told it may. Here the contents are a cache's working files, and losing them is fine. For a pod that keeps something you need in an `emptyDir`, this refusal is the last warning you get.
- **The drain that failed still closed the node.** `SchedulingDisabled`: the first thing a drain does is cordon, and a refusal doesn't undo it. A half-finished drain is either finished or uncordoned. Left alone, it quietly takes a node out of your cluster.

## 9. Pin the cluster's own version

Part 1 step 6 pinned the Python image. The cluster's version is the bigger variable in a repository other people will clone. Ask your running cluster what it's made of:

```bash
docker inspect lab-control-plane --format '{{.Config.Image}}'
```

**You should see:**

```text
kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5
```

Put that line, the one your own command printed, under each node in `kind-cluster.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    image: kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5
  - role: worker
    image: kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5
  - role: worker
    image: kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5
```

**Why:** without it, the Kubernetes version is whatever the installed `kind` picks by default, and it changes when `kind` is upgraded. The part after `@` names the exact image, so the same file gives the same cluster next year. It takes effect the next time the cluster is created, which is the next step.

## 10. The whole lab from the folder

This deletes your cluster on purpose. Everything that matters is in the folder now, and this is the step that proves it.

```bash
kind delete cluster --name lab
```

```bash
kind create cluster --name lab --config kind-cluster.yaml
```

```bash
kubectl apply -k .
```

```bash
kubectl -n lab rollout status statefulset/varnish --timeout=180s
```

```bash
kubectl -n lab get pods -o wide
```

**You should see** `namespace/lab created` first, then every ConfigMap, Service, Deployment and the StatefulSet `created`, and the six pods `Running`. One command built the lab. `kubectl apply -k load/` is the second, and it starts a six-minute run the moment you type it, so type it when you want a measurement.

Then set your terminal's namespace again, since the new cluster's context has none:

```bash
kubectl config set-context --current --namespace=lab
```

**Why:** at the end of Part 2 the rebuild took four commands after creating the cluster, and two of them existed only because the namespace and the load script lived outside the folder. Both are inside it now.

## 11. Commit

```bash
git add 02-origin.yaml 04-varnish.yaml 09-router.yaml kustomization.yaml kind-cluster.yaml load/kustomization.yaml load-via-service/kustomization.yaml results.md
```

```bash
git commit -m "Probes, namespace, cache memory and the load script made explicit; the lab rebuilds from the folder"
```

The `git mv` and `git rm` in step 7 staged their own changes.

## What this doesn't cover

Anything under failure. In step 4 one drain could take both origins and both routers at the same moment, and nothing in the folder forbids it. Part 4 covers that.

**Next:** [Part 4: kill one under load](4-kill-one-under-load.md)
