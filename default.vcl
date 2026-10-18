vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
}

sub vcl_backend_response {
    set beresp.grace = 0s;
}

sub vcl_recv {
    set req.http.Host = "lab";
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
