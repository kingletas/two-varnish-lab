# Contributing

Thanks for looking.

## The gate

```bash
make check
```

That's everything a commit has to pass, and it's what CI runs. It needs ruff,
kubectl and Docker, and no cluster.

## What a change should look like

- One concern per pull request, with the reasoning in the description.
- `make check` green.
- A change that moves a number says so, with the run that measured it: the
  load's summary and its timeline, on a cluster built from the changed files.
- An entry in `CHANGELOG.md` under a new heading, saying what changed for
  somebody using this rather than what the diff did.
- Comments say what the code does or what it guards against, in a sentence or
  two. History belongs in the commit message and the changelog.

## Security

Don't open a public issue for a vulnerability.
[SECURITY.md](SECURITY.md) has the reporting route.
