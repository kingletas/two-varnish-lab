# Part 3: two Varnish, and the problem

**What you have at the end:** two Varnish caches with names that never change, a router in front that sends each URL to the same cache every time, one mistake in the router's config that broke nothing you could see, and **the first two of the lab's three numbers.**

**Start from Part 2's cluster.** If you deleted it, the folder brings it back, and typing this again is practice, not a chore:

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

## 1. One page under two names

Part 3 puts a second name in front of Varnish. Before it does, see what a name does to a cache. Ask for `/shoes` three times, the first two saying the site is called `a` and the third saying it's called `b`:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for h in a a b; do wget -S -qO- --header "Host: $h" http://varnish/shoes 2>&1 | grep X-Cache; done'
```

**You should see:**

```text
  X-Cache: MISS
  X-Cache: HIT
  X-Cache: MISS
```

A miss, a hit, and then a miss for a page the cache already holds. Varnish files every page under two things together: the URL and the `Host` header, the name the client used. `/shoes` on `a` and `/shoes` on `b` are two pages to it.

That has been harmless so far, because everything called Varnish by one name. From step 7 the load reaches it through a router, under the router's name. So make the name not matter. In `default.vcl`, add one line at the top of `vcl_recv`:

```vcl
sub vcl_recv {
    set req.http.Host = "lab";
    if (req.url == "/healthz") {
        return (synth(200, "ok"));
    }
}
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/varnish
```

Then the same three requests again:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for h in a a b; do wget -S -qO- --header "Host: $h" http://varnish/shoes 2>&1 | grep X-Cache; done'
```

**You should see:**

```text
  X-Cache: MISS
  X-Cache: HIT
  X-Cache: HIT
```

**Why:**

- **The cache key is the URL plus the `Host`.** That's Varnish's built-in rule, and it's the right one for a server that holds several sites. This lab has one site, so one fixed name is the honest setting.
- **The first answer is a miss again** because the VCL changed, Kustomize gave the ConfigMap a new name, and the Deployment rolled to a new pod with an empty cache. You didn't restart anything. That's Part 2 step 6 doing its job.
- **Why here and not in the router:** the cache is the thing that cares, so the cache is where it's fixed. A second router, or someone calling Varnish directly, can't undo it.
- **Both measurements in this part now run on the same VCL.** Change it between them and you wouldn't know which change moved the number.

## 2. Scale Varnish to two

The file is the truth, so the copy count changes in the file. In `04-varnish.yaml`, change `replicas: 1` to `replicas: 2`, so the top of its `spec` reads:

```yaml
spec:
  replicas: 2
  selector:
    matchLabels:
      app: varnish
```

Then:

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
configmap/varnish-vcl-55hh5tc65c unchanged
service/origin unchanged
service/varnish unchanged
deployment.apps/origin unchanged
deployment.apps/varnish configured
deployment "varnish" successfully rolled out
NAME                       READY   STATUS    RESTARTS   AGE   IP           NODE          NOMINATED NODE   READINESS GATES
origin-75b8688f98-kt5tk    1/1     Running   0          39s   10.244.2.4   lab-worker2   <none>           <none>
origin-75b8688f98-t8v6s    1/1     Running   0          39s   10.244.2.2   lab-worker2   <none>           <none>
varnish-5946b469bb-2m7xm   1/1     Running   0          12s   10.244.1.3   lab-worker    <none>           <none>
varnish-5946b469bb-6gksn   1/1     Running   0          1s    10.244.2.5   lab-worker2   <none>           <none>
```

Two `varnish-…` pods. The older one has a warm cache and the new one an empty one.

**Why:**

- **`kubectl scale` would work, and the file would still say 1.** The next `kubectl apply -k .` would quietly take you back to one. Same lesson as `rollout undo` in Part 1 step 8.
- **Which node each landed on is luck.** Two on one worker is allowed, and Part 4 is where you take that choice away from luck.

## 3. See the problem with six requests

Ask for a page no cache has seen, six times, through the `varnish` Service:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2 3 4 5 6; do wget -S -qO- http://varnish/spread 2>&1 | grep X-Cache; done'
```

**You should see** something like this. The order is luck, and so is whether you see one miss or two:

```text
  X-Cache: MISS
  X-Cache: MISS
  X-Cache: HIT
  X-Cache: HIT
  X-Cache: HIT
  X-Cache: HIT
```

**Two misses for one page**, one for each cache that had not seen `/spread`. Never more than two. If all six happened to land on one pod you see a single miss; ask again for `/spread2`.

**Why:**

- **This is Part 1 step 7, now costing something.** The Service picks a pod for each new connection and never reads the URL. For the origin that was harmless. For caches it means every page has to be fetched once per cache.
- **A Service works below HTTP.** It moves connections, and the URL is inside the connection where the Service doesn't look. So no setting on this Service can fix it. Its one sticky option, session affinity, sticks by the client's address: it would send everything from one client to one cache, which is a different way of being wrong.

## 4. Measure number 1: two Varnish, spread by the Service

**Write your guess down first.** One cache gave 600 misses in 7200 requests. With two caches that each have to learn every page, how many misses, and what hit rate?

The load is Part 2's, unchanged: you don't touch `load.py` or `07-load-job.yaml` in this step. Only the number of caches changed, so only that can explain a difference.

Every measurement from here on is the same cycle. Clear the last run's Job, and make both caches cold:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl rollout restart deployment/varnish
```

```bash
kubectl rollout status deployment/varnish
```

Count what the origin has served so far. Its log only grows, so you count before and after and subtract:

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

Run the load and read its answer:

```bash
kubectl apply -f 07-load-job.yaml
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

Then each Varnish's own counters. There are two pods now and their names are random, so a loop asks each one:

```bash
for p in $(kubectl get pods -l app=varnish -o name); do echo $p; kubectl exec $p -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss; done
```

And the origin again:

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

**You should see**, with your own pod names:

```text
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6025 miss=1175
other=0
hit_rate=0.837
p50_ms=1
p95_ms=303
pod/varnish-5cc7874d87-fxhmk
MAIN.cache_hit            3007         8.11 Cache hits
MAIN.cache_miss            586         1.58 Cache misses
pod/varnish-5cc7874d87-rvm89
MAIN.cache_hit            3018         8.09 Cache hits
MAIN.cache_miss            589         1.58 Cache misses
1175
```

**0.837, down from 0.917.** These numbers are one sample run, made while this guide was written, not the published runs; the plain-Service run in [results.md](../../results.md) gave `miss=1178` and `hit_rate=0.836`. In the sample, the misses went from 600 to 1175, and the three witnesses still agree: 589 and 586 add up to 1175, and the origin served 1175 more pages than before the run.

Now run the whole cycle a second time:

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
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
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
for p in $(kubectl get pods -l app=varnish -o name); do echo $p; kubectl exec $p -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss; done
```

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

In the run behind this guide the second gave `miss=1169` and `hit_rate=0.838`, six requests apart from the first. An earlier pair of runs gave 1175 and 1176. Expect yours within a few requests of these, and not equal to each other.

**Why:**

- **Nearly double, and the "nearly" is about time, not luck.** One cache is asked for each page about every 10 seconds, so when a copy expires it's fetched again almost at once, and three lifetimes of 120 seconds fit in a 360-second run: 200 pages, three fetches each, 600.

  Behind the Service, each cache is asked for a given page only about every 20 seconds. It waits that long, on average, for its first request, and again after each expiry. Two lifetimes take 240 seconds, which leaves 120 for three waits, and about 6 times in 100 the three waits add up to more than that, so the third fetch never happens before the load ends.

  400 page-and-cache pairs, three fetches each, less 6 in 100 of the third: 1175. That's arithmetic that fits the runs, not something the lab measured. **So number 1 belongs to a six-minute run:** a longer one wouldn't be short by the same share.
- **The two runs differ by a few requests** where Part 2's two runs matched exactly. The URLs and their order are fixed by the seed, but which cache each request lands on is the Service's pick, and that isn't repeatable. A number with a random part gets written down with both runs, not as one figure.
- **The cost isn't only misses.** The same 200 pages now sit in both caches. Here they are tiny. With real pages, two caches that each hold everything hold half as many different pages as they could.
- **`p95` didn't move** (302 ms with one cache, 303 ms with two) even though the misses doubled. About one request in six is a miss now, still more than one in twenty, so the 95th percentile was already a miss in Part 2 and still is. A latency number can sit still while the thing behind it gets twice as bad. Remember that when Part 4 sets a latency target.

## 5. Give each cache a name that doesn't change

The fix is to send each URL to the same cache every time. That needs something a Deployment doesn't give you: a way to say *which* cache. Its pods are named with random letters and replaced by new names, on purpose, because they are meant to be interchangeable. Caches that each hold different pages aren't interchangeable.

A *StatefulSet* is the kind for that. Its pods are numbered: `varnish-0`, `varnish-1`. Delete one and the replacement gets the same name. A StatefulSet needs a *headless Service* beside it: a Service with no address of its own, whose job is to give each pod a DNS name.

A Deployment can't be turned into a StatefulSet. They are different kinds, so the old one goes first:

```bash
kubectl delete job load --ignore-not-found
```

```bash
kubectl delete deployment varnish
```

In `04-varnish.yaml`, change two things and leave everything else: `kind: Deployment` becomes `kind: StatefulSet`, and `spec` gains one line, `serviceName`. The top of the file now reads:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: varnish
  labels:
    app: varnish
spec:
  serviceName: varnish-pods
  replicas: 2
  selector:
    matchLabels:
      app: varnish
```

Type this as `08-varnish-pods-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: varnish-pods
  labels:
    app: varnish
spec:
  clusterIP: None
  selector:
    app: varnish
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Add `- 08-varnish-pods-service.yaml` to the `resources` list in `kustomization.yaml`, after `05-varnish-service.yaml`. The file now reads:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - 02-origin.yaml
  - 03-origin-service.yaml
  - 04-varnish.yaml
  - 05-varnish-service.yaml
  - 08-varnish-pods-service.yaml
configMapGenerator:
  - name: origin-code
    files:
      - server.py
  - name: varnish-vcl
    files:
      - default.vcl
```

Then:

```bash
kubectl apply -k .
```

```bash
kubectl rollout status statefulset/varnish
```

```bash
kubectl get pods -o wide
```

```bash
kubectl get service
```

**You should see:**

```text
configmap/origin-code-hb96279hb7 unchanged
configmap/varnish-vcl-55hh5tc65c unchanged
service/origin unchanged
service/varnish unchanged
service/varnish-pods created
deployment.apps/origin unchanged
statefulset.apps/varnish created
Waiting for 2 pods to be ready...
Waiting for 1 pods to be ready...
partitioned roll out complete: 2 new pods have been updated...
NAME                      READY   STATUS    RESTARTS   AGE   IP            NODE          NOMINATED NODE   READINESS GATES
origin-75b8688f98-kt5tk   1/1     Running   0          13m   10.244.2.4    lab-worker2   <none>           <none>
origin-75b8688f98-t8v6s   1/1     Running   0          13m   10.244.2.2    lab-worker2   <none>           <none>
varnish-0                 1/1     Running   0          12s   10.244.2.8    lab-worker2   <none>           <none>
varnish-1                 1/1     Running   0          7s    10.244.1.11   lab-worker    <none>           <none>
NAME           TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
origin         ClusterIP   10.96.254.56   <none>        80/TCP     13m
varnish        ClusterIP   10.96.31.130   <none>        80/TCP     13m
varnish-pods   ClusterIP   None           <none>        8080/TCP   13s
```

`varnish-0` and `varnish-1`, no random letters. And a fourth Service whose `CLUSTER-IP` is `None`.

Now ask the cluster's DNS what the two kinds of Service answer:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'sleep 2; nslookup varnish.lab.svc.cluster.local; nslookup varnish-pods.lab.svc.cluster.local; nslookup varnish-0.varnish-pods.lab.svc.cluster.local'
```

**You should see**, with your own addresses and a blank line more or less:

```text
Server:		10.96.0.10
Address:	10.96.0.10:53

Name:	varnish.lab.svc.cluster.local
Address: 10.96.31.130

Server:		10.96.0.10
Address:	10.96.0.10:53

Name:	varnish-pods.lab.svc.cluster.local
Address: 10.244.2.8
Name:	varnish-pods.lab.svc.cluster.local
Address: 10.244.1.11

Server:		10.96.0.10
Address:	10.96.0.10:53

Name:	varnish-0.varnish-pods.lab.svc.cluster.local
Address: 10.244.2.8
```

**The `sleep 2` is for `kubectl`, not for DNS.** Three lookups finish faster than `kubectl` can attach to the pod. When it loses that race it prints `warning: couldn't attach to pod/probe, falling back to streaming logs` and you may get the whole answer twice. Behind this guide that happened in two of three tries without the pause and in neither of two with it. Nothing is wrong when it does.

The plain Service answers with one address, its own, which hides the pods. The headless one answers with the pods' addresses. And each pod has a name of its own under it.

Delete one and watch the name come back:

```bash
kubectl delete pod varnish-0
```

```bash
kubectl get pods -l app=varnish -o wide
```

**You should see:**

```text
pod "varnish-0" deleted from lab namespace
NAME        READY   STATUS    RESTARTS   AGE   IP            NODE          NOMINATED NODE   READINESS GATES
varnish-0   1/1     Running   0          15s   10.244.2.9    lab-worker2   <none>           <none>
varnish-1   1/1     Running   0          28s   10.244.1.11   lab-worker    <none>           <none>
```

Same name, new address, age in seconds. The address changed and the name did not, which is why the router will be given names.

**Why:**

- **`clusterIP: None` is what makes a Service headless.** No address, no spreading. It only publishes who is behind it.
- **`serviceName` ties the StatefulSet to that Service**, and that's where `varnish-0.varnish-pods` comes from: the pod's name, a dot, the headless Service's name.
- **Port 8080 here, not 80.** There's no Service address to translate a port, so a client reaches the pod directly on the port the pod listens on.
- **The old `varnish` Service still works.** Its selector is the label `app: varnish`, and the new pods carry it. A Service doesn't care what kind made the pods.
- **A StatefulSet starts its pods in order and replaces them one at a time, highest number first.** For a cache that's what you want: never both empty at once.
- **What a StatefulSet usually comes with is a disk per pod.** Not here: the cache lives in memory and a new `varnish-0` starts empty. You're using the StatefulSet for the name alone.

## 6. Write the router's config, and check it before the cluster sees it

The router is HAProxy. Its job is one line of arithmetic: turn the URL into a number, and let the number pick the cache. The same URL gives the same number, so it picks the same cache.

Type this as `haproxy.cfg`:

```haproxy
global
    maxconn 1000
    log stdout format raw local0

defaults
    mode http
    log global
    option httplog
    option dontlognull
    option redispatch
    retries 3
    timeout connect 2s
    timeout client 30s
    timeout server 30s

resolvers cluster
    parse-resolv-conf
    hold valid 5s
    timeout resolve 1s
    timeout retry 1s

frontend web
    bind :8080
    monitor-uri /healthz
    monitor fail if { nbsrv(caches) lt 1 }
    default_backend caches

backend caches
    balance uri
    hash-type consistent
    option httpchk GET /healthz
    default-server check inter 2s fall 3 rise 2 resolvers cluster resolve-prefer ipv4 init-addr none
    server varnish-0 "varnish-0.varnish-pods.${POD_NAMESPACE}.svc.cluster.local:8080"
    server varnish-1 "varnish-1.varnish-pods.${POD_NAMESPACE}.svc.cluster.local:8080"

listen stats
    bind :8404
    stats enable
    stats uri /
```

Check it on your laptop, with the same image the cluster will run:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

**You should see:**

```text
Configuration file is valid
```

**Why, block by block:**

- **`balance uri` with `hash-type consistent`** is the whole idea. `balance uri` hashes the path of the URL to choose a server. `consistent` decides what happens when a server goes away: only the URLs that belonged to it move, and the rest stay where they were.

  With two caches you can't see the difference, because every URL goes to the survivor either way. With three or more, the default would move many URLs between the caches that are still up, and they'd go cold too.
- **The servers are listed by name, one line each.** HAProxy can also fill its list from DNS by itself. Then which pod lands in which slot depends on the order the DNS answer arrived in, and two routers can disagree about which cache owns a URL. Nothing errors when they do; the hit rate just sinks back toward step 4's. Two names typed out can't disagree.
- **The full name, ending in `.svc.cluster.local`.** Inside a pod, `varnish-0.varnish-pods` works because the pod's DNS settings add the rest. HAProxy does its own lookups and doesn't add anything, so it gets the whole name. `${POD_NAMESPACE}` is filled in from the pod's environment; step 7 puts it there.
- **`resolvers cluster` and `init-addr none`.** A pod's address changes when it's replaced (you just saw it). `resolvers` tells HAProxy to keep asking the cluster's DNS, and `init-addr none` lets it start before a name resolves, with that server marked down, where its default is to refuse to start.
- **`option httpchk GET /healthz`, `inter 2s fall 3 rise 2`.** HAProxy asks each cache if it's well every 2 seconds. Three misses and the cache is out; two answers and it's back. So a dead cache keeps receiving requests for up to about 6 seconds. That number is a choice, and Part 4 measures what it costs.
- **`option redispatch` and `retries 3`.** A request whose cache doesn't answer is tried again and then sent to the other cache. A dead cache should cost cold pages, not errors.
- **`monitor-uri /healthz`** is the router's own health page, answered by HAProxy itself. It fails only when no cache at all is up. One cache down isn't a reason to take the router out of service.
- **`listen stats`** is HAProxy's own status page on a second port. You read it in step 7. It's how you ask the router what it believes.
- **`option dontlognull`** keeps connections that send nothing out of the log. The cluster's liveness probe in step 7 is exactly that, every 10 seconds.
- **`-c` checks the file and exits. `-V` makes it say so.** Without `-V` a good file prints nothing at all, and silence is a poor thing to trust.

Now see the check say no. Break a word on purpose, so the `backend caches` section starts:

```haproxy
backend caches
    balance urii
```

Run the check again:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

Read the line number it gives you, and put the word back, so the section starts as it did:

```haproxy
backend caches
    balance uri
```

Run the check once more, and it says `Configuration file is valid` again:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

**Why HAProxy, and what was ruled out.** It's the least typing that shows the mechanism, its health checks are written out, and it will tell you per server what it sent where. NGINX with `hash $request_uri consistent` was the close second.

On a real cluster today the answer is a Gateway API controller that supports a consistent-hash policy: it does this same thing and hides it, which is the wrong trade for a lab whose point is to see it. The Service's session affinity isn't an option at all, for the reason in step 3.

## 7. Put the router on the cluster

Type this as `09-router.yaml`:

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
      containers:
        - name: haproxy
          image: haproxy:3.2-alpine
          args: ["haproxy", "-f", "/cfg/haproxy.cfg"]
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
          livenessProbe:
            tcpSocket:
              port: http
            periodSeconds: 10
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

Type this as `10-router-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: router
  labels:
    app: router
spec:
  selector:
    app: router
  ports:
    - name: http
      port: 80
      targetPort: http
```

`kustomization.yaml` gains two resources and a third generated ConfigMap. It now reads:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
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

```bash
kubectl rollout status deployment/router
```

```bash
kubectl logs deploy/router
```

**You should see:**

```text
Found 2 pods, using pod/router-57c99c7447-qg5vh
[NOTICE]   (1) : Initializing new worker (15)
[NOTICE]   (1) : Loading success.
[WARNING]  (15) : caches/varnish-1: IP changed from '(none)' to '10.244.1.11' by 'cluster/10.96.0.10'.
[WARNING]  (15) : caches/varnish-0: IP changed from '(none)' to '10.244.2.9' by 'cluster/10.96.0.10'.
[WARNING]  (15) : Server caches/varnish-1 ('varnish-1.varnish-pods.lab.svc.cluster.local') is UP/READY (resolves again).
[WARNING]  (15) : Server caches/varnish-1 administratively READY thanks to valid DNS answer.
[WARNING]  (15) : Server caches/varnish-0 ('varnish-0.varnish-pods.lab.svc.cluster.local') is UP/READY (resolves again).
[WARNING]  (15) : Server caches/varnish-0 administratively READY thanks to valid DNS answer.
```

Read it as a story: each server starts with no address, DNS answers, and the server comes up. Now ask each router what it believes. The status page speaks CSV, and three of its columns are enough: the backend, the server, and its state:

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

**You should see:**

```text
pod/router-57c99c7447-9cfwm
# pxname,svname,status
web,FRONTEND,OPEN
caches,varnish-0,UP
caches,varnish-1,UP
caches,BACKEND,UP
stats,FRONTEND,OPEN
stats,BACKEND,UP
pod/router-57c99c7447-qg5vh
# pxname,svname,status
web,FRONTEND,OPEN
caches,varnish-0,UP
caches,varnish-1,UP
caches,BACKEND,UP
stats,FRONTEND,OPEN
stats,BACKEND,UP
```

Both routers, both caches `UP`.

**One thing changes in how you look, before you look.** Ask for one page directly, then through the router, and read all the response headers both times:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'wget -S -qO- http://varnish/shoes 2>&1 | head -14'
```

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'wget -S -qO- http://router/shoes 2>&1 | head -14'
```

**You should see**, with your own dates and pod names:

```text
  HTTP/1.1 200 OK
  Server: BaseHTTP/0.6 Python/3.13.16
  Date: Thu, 12 Mar 2026 14:20:07 GMT
  Content-Type: text/plain; charset=utf-8
  Content-Length: 54
  Cache-Control: public, s-maxage=120
  X-Varnish: 36
  Age: 0
  Via: 1.1 varnish-0 (Varnish/7.7)
  Accept-Ranges: bytes
  X-Cache: MISS
  Connection: close
  
path=/shoes pod=origin-75b8688f98-hwhrz made=14:20:07
```

```text
  HTTP/1.1 200 OK
  server: BaseHTTP/0.6 Python/3.13.16
  date: Thu, 12 Mar 2026 14:20:10 GMT
  content-type: text/plain; charset=utf-8
  content-length: 54
  cache-control: public, s-maxage=120
  x-varnish: 30
  age: 0
  via: 1.1 varnish-1 (Varnish/7.7)
  accept-ranges: bytes
  x-cache: MISS
  connection: close
  
path=/shoes pod=origin-75b8688f98-hwhrz made=14:20:10
```

The same headers, and every name in lower case the second time. HAProxy writes header names in lower case when it passes a response on. HTTP says a header's name isn't case-sensitive, so nothing is wrong. But `grep X-Cache` is case-sensitive, and it finds nothing in the second answer. **From here on the command says `grep -i x-cache`.** The second answer may say `HIT` for you, if the router picked the cache that had just answered the first.

Through the router, the old command prints a single line, `pod lab/probe terminated (Error)`, while the caches' own counters show every request served. `grep` found no line, a `grep` that finds nothing exits with 1, and the pod reports its last command's exit code. The error is in the tool you look with, not in the router.

HAProxy can be told to restore the capitals for a client that can't cope (`h1-case-adjust`); the better fix is a client that doesn't care, and `-i` is that.

Now ask for a new page six times, the test from step 3, but through the router:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2 3 4 5 6; do wget -S -qO- http://router/spread3 2>&1 | grep -i x-cache; done'
```

**You should see:**

```text
  x-cache: MISS
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
```

**One miss, then five hits.** Six requests, spread over two routers by a Service that still doesn't read URLs, and all six reached the same cache.

**Why, block by block:**

- **The router is a Deployment, and that's right.** The two routers hold nothing. Either can answer any request, because both work out the same number from the same URL. Interchangeable pods are what a Deployment is for, and the contrast with step 5 is the point.
- **Two replicas.** One router would be a new single point of failure, placed in front of the two caches you added to avoid one.
- **`args` and not `command`.** The image has a start script that prepares things and then runs what it's given. `args` keeps the script and hands it our command line. In Part 2 you replaced Varnish's whole command on purpose; here there was no reason to.
- **`POD_NAMESPACE` comes from `fieldRef`.** The pod is told its own namespace by the cluster and passes it to HAProxy as an environment variable. The config never has `lab` typed into it, so the same file works in any namespace.
- **Readiness asks `/healthz`; liveness only checks that the port opens.** Readiness may depend on the caches: a router with no cache to send to should stop getting traffic. Liveness must not: restarting the router doesn't bring a cache back. A liveness probe asks about the process and nothing outside it.
- **`cut -d, -f1,2,18`** picks three of the status page's many columns. `BACKEND` is the group as a whole.

## 8. Break it: one letter missing, and nothing fails

You type everything, so sooner or later you drop a letter. Do it on purpose. In `haproxy.cfg`, in the last `server` line, change `varnish-1.varnish-pods` to `varnsh-1.varnish-pods`, so the two `server` lines read:

```haproxy
    server varnish-0 "varnish-0.varnish-pods.${POD_NAMESPACE}.svc.cluster.local:8080"
    server varnish-1 "varnsh-1.varnish-pods.${POD_NAMESPACE}.svc.cluster.local:8080"
```

Run the check from step 6, then apply:

```bash
docker run --rm -e POD_NAMESPACE=lab -v "$PWD/haproxy.cfg:/cfg/haproxy.cfg:ro" haproxy:3.2-alpine haproxy -c -V -f /cfg/haproxy.cfg
```

```bash
kubectl apply -k .
```

```bash
kubectl rollout status deployment/router
```

**You should see**, with a `Waiting` line more or less:

```text
Configuration file is valid
configmap/origin-code-hb96279hb7 unchanged
configmap/router-config-b89h76726c created
configmap/varnish-vcl-55hh5tc65c unchanged
service/origin unchanged
service/router unchanged
service/varnish unchanged
service/varnish-pods unchanged
deployment.apps/origin unchanged
deployment.apps/router configured
statefulset.apps/varnish unchanged
Waiting for deployment "router" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "router" rollout to finish: 1 old replicas are pending termination...
deployment "router" successfully rolled out
```

The check passes. The rollout finishes. Ask for a page and it answers:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2 3 4 5 6; do wget -S -qO- http://router/spread4 2>&1 | grep -i x-cache; done'
```

```text
  x-cache: MISS
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
  x-cache: HIT
```

Everything you would normally look at says this is fine. Now ask the routers what they believe:

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

**You should see:**

```text
pod/router-9bdf8d4db-29d2b
# pxname,svname,status
web,FRONTEND,OPEN
caches,varnish-0,UP
caches,varnish-1,MAINT (resolution)
caches,BACKEND,UP
stats,FRONTEND,OPEN
stats,BACKEND,UP
pod/router-9bdf8d4db-5nt5t
# pxname,svname,status
web,FRONTEND,OPEN
caches,varnish-0,UP
caches,varnish-1,MAINT (resolution)
caches,BACKEND,UP
stats,FRONTEND,OPEN
stats,BACKEND,UP
```

**`MAINT (resolution)`.** Both routers have set `varnish-1` aside, because the name they were given for it doesn't resolve to anything. The group, `BACKEND`, is still `UP`, since one cache is enough to answer, so the readiness probe passed, the rollout finished, and `varnish-0` served every request.

So the six requests went to one cache. One miss and five hits is also what a working router gives for one page, so that test couldn't show the typo. Only the status page shows it.

The router's log tells you the same thing by what it leaves out:

```bash
kubectl logs deploy/router | head -12
```

**You should see** something like:

```text
Found 2 pods, using pod/router-9bdf8d4db-5nt5t
[NOTICE]   (1) : Initializing new worker (15)
[NOTICE]   (1) : Loading success.
[WARNING]  (15) : caches/varnish-0: IP changed from '(none)' to '10.244.1.6' by 'cluster/10.96.0.10'.
[WARNING]  (15) : Server caches/varnish-0 ('varnish-0.varnish-pods.lab.svc.cluster.local') is UP/READY (resolves again).
[WARNING]  (15) : Server caches/varnish-0 administratively READY thanks to valid DNS answer.
```

Three lines about `varnish-0` finding its address, and not one about `varnish-1`. HAProxy says when a name starts to resolve. It doesn't say that a name never did.

Put the letter back, so the last `server` line reads as it did:

```haproxy
    server varnish-1 "varnish-1.varnish-pods.${POD_NAMESPACE}.svc.cluster.local:8080"
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

**You should see**, with a `Waiting` line more or less:

```text
Configuration file is valid
configmap/origin-code-hb96279hb7 unchanged
configmap/router-config-hgt989dh28 unchanged
configmap/varnish-vcl-55hh5tc65c unchanged
service/origin unchanged
service/router unchanged
service/varnish unchanged
service/varnish-pods unchanged
deployment.apps/origin unchanged
deployment.apps/router configured
statefulset.apps/varnish unchanged
deployment "router" successfully rolled out
```

The router's ConfigMap says `unchanged`, not `created`. Your fixed file hashes to the same name as before the typo, and that ConfigMap was never deleted, so the Deployment goes back to its old pod template, as the Varnish one did in Part 2 step 7. Your new router pods' names start with the same letters as in step 7.

Now read the status page again, and repeat the command until both caches say `UP` on both routers:

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

**You should see**, under each router's own pod name:

```text
# pxname,svname,status
web,FRONTEND,OPEN
caches,varnish-0,UP
caches,varnish-1,UP
caches,BACKEND,UP
stats,FRONTEND,OPEN
stats,BACKEND,UP
```

**Why:**

- **The syntax check can't know a name is wrong.** `varnsh-1` is a perfectly good name for a server that doesn't exist. `init-addr none` is what let HAProxy start anyway, and you chose it for a good reason in step 6. The same setting that survives a pod being replaced also survives a typo.
- **Nothing failed, because you built it not to.** The router stays ready with one cache. Requests for the missing cache go to the other. Every safety you added in step 6 worked, and together they hid the mistake.
- **And the hit rate wouldn't have told you.** One cache receiving every URL gives 0.917, the best number in the lab, from a setup with no second cache in it. A good number isn't proof that the system is the one you drew.
- **So look at what the router believes, not only at whether it answers.** That's what the status page is for, and it's the first thing to read after any change to this file.

## 9. Measure number 2: each URL always to the same Varnish

**Write your guess down.** Two caches, and each URL always goes to the same one. How many misses?

One line changes in the load. In `07-load-job.yaml`, the `TARGET` value becomes `http://router`, so the first entry under `env` reads:

```yaml
          env:
            - name: TARGET
              value: "http://router"
```

The cycle is step 4's, with two differences. The caches are a StatefulSet now, and after they restart you wait for the routers to see them:

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

**Don't start the load until all four server lines say `UP`.** A restarted cache has a new address, and each router has to look it up and see two good health checks before it sends anything there. In the run behind this guide that took about four seconds after the rollout finished.

On the way one line read `UP 1/3`: still counted as up, but failing its checks, because that router was still asking the old address. A load started in those seconds sends some URLs to the wrong cache, and the number you get is neither of the two you're measuring.

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
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

The pods have names now, so no loop:

```bash
kubectl exec varnish-0 -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

```bash
kubectl exec varnish-1 -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

**You should see:**

```text
urls=200 rate=20.0/s seconds=360 workers=8 seed=1
requests=7200 hit=6600 miss=600
other=0
hit_rate=0.917
p50_ms=1
p95_ms=302
MAIN.cache_hit            2733         7.43 Cache hits
MAIN.cache_miss            249         0.68 Cache misses
MAIN.cache_hit            3867        10.31 Cache hits
MAIN.cache_miss            351         0.94 Cache misses
```

**600 misses and 0.917: one cache's number, from two caches.** Every page is fetched once per lifetime again, because each page lives in exactly one place.

Run the whole cycle a second time. Repeat the status command until all four server lines say `UP` before you start the load:

```bash
kubectl delete job load
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
kubectl apply -f 07-load-job.yaml
```

```bash
kubectl wait --for=condition=complete job/load --timeout=600s
```

```bash
kubectl logs job/load
```

```bash
kubectl exec varnish-0 -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

```bash
kubectl exec varnish-1 -- varnishstat -n /tmp/varnish -1 -f MAIN.cache_hit -f MAIN.cache_miss
```

```bash
kubectl logs -l app=origin --tail=-1 | grep -c 'GET /p/'
```

In the run behind this guide the second matched the first to the request, per cache as well: `miss=600`, `hit_rate=0.917`, and 249 and 351 misses on the two caches again.

**Why:**

- **The two caches aren't even, and that isn't a fault.** `varnish-0` missed 249 times and `varnish-1` 351. That's 83 pages on one and 117 on the other, each fetched three times: 83 and 117 make the 200. Divide each cache's misses by three, for the three lifetimes, and you have how many of the 200 pages it owns. A hash spreads a great many URLs evenly; 200 isn't a great many.
- **Write the split down.** In Part 4 you kill one cache. Which one you kill decides how many pages go cold, and now you know the two answers before you do it.
- **This time the two runs match exactly,** where step 4's differed by six. Nothing in the path is a coin toss any more: the seed fixes the URLs, and the hash fixes where each one goes.
- **The Service in front of the routers still spreads at random,** and it doesn't matter, because both routers do the same arithmetic. That's the property the named servers bought you in step 6.
- **`rollout restart` on a StatefulSet goes one pod at a time, highest number first,** and waits for each to be ready. You saw it in the pause between the two pods.

## 10. Replace a cache and see what survives

Ask for one page twice, and find out which cache holds it. The router's log names the server on every line:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2; do wget -S -qO- http://router/p/7 2>&1 | grep -i x-cache; done'
```

```bash
kubectl logs -l app=router --tail=-1 | grep 'GET /p/7 ' | tail -2
```

**You should see:**

```text
  x-cache: MISS
  x-cache: HIT
10.244.2.13:41584 [12/Mar/2026:14:20:47.231] web caches/varnish-1 0/0/0/301/301 200 315 - - ---- 1/1/0/0/0 0/0 "GET /p/7 HTTP/1.1"
10.244.2.13:41596 [12/Mar/2026:14:20:47.535] web caches/varnish-1 0/0/0/0/0 200 317 - - ---- 1/1/0/0/0 0/0 "GET /p/7 HTTP/1.1"
```

`caches/varnish-1`: this page belongs to `varnish-1`. It did in both runs behind this guide, and since the choice is worked out from the URL and the config, yours very likely says the same. Your first line may read `HIT` if the load from step 9 ended less than two minutes ago and the page is still in the cache.

Delete that cache (use the name your log shows), then read the routers' status, as in step 7, until it says `UP` on both:

```bash
kubectl delete pod varnish-1
```

```bash
for p in $(kubectl get pods -l app=router -o name); do echo $p; kubectl exec $p -- wget -qO- "http://127.0.0.1:8404/;csv" | cut -d, -f1,2,18; done
```

```bash
kubectl get pods -l app=varnish -o wide
```

**You should see**, once it has settled:

```text
NAME        READY   STATUS    RESTARTS   AGE   IP            NODE          NOMINATED NODE   READINESS GATES
varnish-0   1/1     Running   0          98s   10.244.1.6    lab-worker    <none>           <none>
varnish-1   1/1     Running   0          12s   10.244.2.14   lab-worker2   <none>           <none>
```

Behind this guide both routers said `UP` again 12 seconds after the delete. If you're quick you catch one of them saying `UP 2/3` on the way: one health check has failed, and the router allows three (`fall 3`) before it calls a server down. The new `varnish-1` was back before the third.

Then ask for the same page twice more, and read the log again:

```bash
kubectl run probe --image=busybox:1.37 --restart=Never --rm -i --quiet -- sh -c 'for i in 1 2; do wget -S -qO- http://router/p/7 2>&1 | grep -i x-cache; done'
```

```bash
kubectl logs -l app=router --tail=-1 | grep 'GET /p/7 ' | tail -2
```

**You should see:**

```text
  x-cache: MISS
  x-cache: HIT
10.244.2.15:47498 [12/Mar/2026:14:21:03.665] web caches/varnish-1 0/0/0/302/302 200 315 - - ---- 1/1/0/0/0 0/0 "GET /p/7 HTTP/1.1"
10.244.2.15:47508 [12/Mar/2026:14:21:03.969] web caches/varnish-1 0/0/0/0/0 200 320 - - ---- 1/1/0/0/0 0/0 "GET /p/7 HTTP/1.1"
```

**The page went back to the same cache by name, and the cache had forgotten it.** The mapping survived the replacement. The pages did not.

**Why:**

- **The name is the identity.** The new pod has a new address, HAProxy's resolver picked it up, and `/p/7` still belongs to the same server line.
- **A cache in memory comes back empty.** Every page that cache owned is cold again, and only those: the other cache's pages were never touched. How many, and for how long under load, is the third number, and Part 4.
- **You did this with no load running, on purpose.** Under load there's a stretch between the pod dying and the router noticing, and requests land in it. That stretch is made of the timers in step 6, and it deserves its own part.

## 11. Write the numbers down

Add four rows to the table in `results.md`, under Part 2's two, with your own values, and under the table the split from step 9. The whole file now reads:

```markdown
# Results

| Part | Setup | URLs | Rate | Seconds | Seed | Run | Hit rate | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 1 | ... | ... | ... |
| 2 | One Varnish, 64m cache, grace 0 | 200 | 20/s | 360 | 1 | 2 | ... | ... | ... |
| 3 | Two Varnish, plain Service | 200 | 20/s | 360 | 1 | 1 | ... | ... | ... |
| 3 | Two Varnish, plain Service | 200 | 20/s | 360 | 1 | 2 | ... | ... | ... |
| 3 | Two Varnish, HAProxy by URL | 200 | 20/s | 360 | 1 | 1 | ... | ... | ... |
| 3 | Two Varnish, HAProxy by URL | 200 | 20/s | 360 | 1 | 2 | ... | ... | ... |

## Which cache owns what

Of the 200 URLs, with HAProxy routing by URL: `varnish-0` owns ..., `varnish-1` owns ....
```

**Check the top of your file while you're there.** A Markdown table needs its first two lines to be a table: the header row, then the row of dashes, `|---|---|` and so on, one group per column. Without the dashes it shows as plain text with bars in it. The whole file starts with `# Results` on its own line.

**Why:** the run behind this guide wrote 0.837, 1 and 303 on the first row, 0.838, 1 and 303 on the second, 0.917, 1 and 302 on the last two, and 83 pages on `varnish-0` and 117 on `varnish-1`. Yours are the ones that go in your file.

## 12. Commit

```bash
git add default.vcl 04-varnish.yaml 07-load-job.yaml 08-varnish-pods-service.yaml haproxy.cfg 09-router.yaml 10-router-service.yaml kustomization.yaml results.md
```

```bash
git commit -m "Two Varnish with stable names behind an HAProxy that routes by URL, and both hit rates measured"
```

## What this doesn't cover

What happens when a cache dies under load, how long the router takes to notice, how many requests land in that gap, and how long the pages take to warm back. That's the third number, and Part 4. Nor real traffic: 200 URLs asked evenly isn't how a store is browsed, and the split between the caches would be different with a different set of URLs.

**Next:** [Between Parts 3 and 4: tighten what you typed](3b-tighten-what-you-typed.md)
