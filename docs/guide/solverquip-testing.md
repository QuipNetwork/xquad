# Testing SolverQuip against a Quip network

`SolverQuip` (the [`xqsa`](../xqsa/) `"quip"` backend, [`xqsa/quip.py`](../xqsa/quip.py))
submits Ising jobs to a live Quip Network chain, waits for a solver fleet to
return a solution, decodes the best one, and runs an energy canary against the
chain's recorded result. The gated live suite
([`xqsa/tests/test_quip_live.py`](../xqsa/tests/test_quip_live.py), pytest marker
`quip`) exercises that whole path end to end against a real chain.

This guide covers running that suite in the two environments you can point it
at:

- a local **DevNet** -- `make localdev` in the sibling `nodes.quip.network`
  repo stands up a self-contained single-validator chain plus a faucet and one
  CPU miner. Fast, offline, and you control the whole stack.
- the public **aglais** network -- a real multi-node deployment with a
  third-party GPU solver fleet and a rolling runtime. This is the target for
  validation the DevNet cannot fully stand in for (real solvers, real timing,
  real runtime upgrades). Its faucet is healthy, so the funded tier runs there
  too.

The older public **TestNet** is retired and is no longer a target. It ran the
H3 suite (sr25519 + ML-DSA-44) that this signing layer no longer speaks, and
its faucet stopped resolving before it was shut down. Nothing in this guide
points at it.

Both are opt-in and gated on `QUIP_RPC_URL`, so they never run in the default
`make test` or in CI. The [MR template](../.gitlab/merge_request_templates/default.md)
lists `make test-quip` as an Optional Check for any change to SolverQuip.

## The make targets

The three quip targets live in the [`Makefile`](../Makefile):

| Target | Runs | Needs a chain? |
| --- | --- | --- |
| `make test-quip-sign` | signing tests ([`test_quip_signing.py`](../xqsa/tests/test_quip_signing.py)) -- pure-Python SCALE / keystore / extrinsic | no |
| `make test-quip-e2e` | live suite ([`test_quip_live.py`](../xqsa/tests/test_quip_live.py)) against a chain | yes |
| `make test-quip` | both of the above (the MR-template Optional Check) | yes (for the e2e leaf) |

`make test-quip-sign` is also what the CI `test:quip` job runs; it needs no
chain and always executes. `make test-quip-e2e` hard-errors when `QUIP_RPC_URL`
is unset (rather than reporting a hollow all-skipped pass), so the aggregate
`make test-quip` requires the devnet env vars below for its e2e leaf. All three
self-install the `[quip]` extra via `uv run --extra quip`.

### Environment variables

| Variable | When | Meaning |
| --- | --- | --- |
| `QUIP_RPC_URL` | required for e2e | chain RPC, `ws://` (DevNet) or `wss://` (aglais). `make test-quip` / `make test-quip-e2e` pass it through explicitly. |
| `QUIP_FAUCET_URL` | optional | faucet **base** URL; the funded fixture POSTs to `<QUIP_FAUCET_URL>/request`. Without it only the read-only connectivity tests run. |
| `QUIP_TOPOLOGY` | optional | registered topology hash for `SolverQuip` to target instead of the chain default. Unlike the rest of this table it configures the solver, not the harness, so it also applies outside the tests. Setting it un-skips the override tier. |
| `QUIP_MINER_PROBE_TIMEOUT` | optional (default 60) | how long the `solving_miner` probe waits before skipping the end-to-end tier. Raise it on a slow or remote fleet. |

Any variable exported in your shell -- or passed as `make VAR=val` -- reaches
pytest, so you can set `QUIP_MINER_PROBE_TIMEOUT` alongside the two the
recipe forwards. On macOS, whose python.org and uv Pythons ship no CA
bundle, `xqsa.quip_metadata.connect` and the suite's faucet requests verify
TLS against certifi's bundle unless `SSL_CERT_FILE`, `SSL_CERT_DIR`, or
`WEBSOCKET_CLIENT_CA_BUNDLE` is set, so aglais needs no CA export.

### The two test tiers

- **submit + lifecycle** (always run against any healthy chain): connectivity,
  the live `propose_job` SCALE / extras contract (a clean submission with no
  `System.ExtrinsicFailed`), the topology fetch, the balance pre-check, the
  timeout path, and expired-no-solution auto-reclaim. These need no returned
  solution.
- **end-to-end** (`TestEndToEnd`): need the fleet to actually solve. They are
  guarded by the `solving_miner` fixture, which proposes a throwaway order and
  waits for a solution; it skips the tier cleanly when no fleet is active. A
  fresh order solves in ~1-2 blocks when a fleet is running.

## Common prerequisites

- `uv sync --extra quip` in this repo (installs `substrate-interface` + the
  `quip-signer` extension). The make targets do this for you via
  `uv run --extra quip`.
- **Apple Silicon:** `quip-signer` 0.3.0 publishes no macOS wheel, so the first
  sync after the pin moved builds it from the sdist and needs a local Rust
  toolchain. Expect minutes, not seconds. Slow here is expected; a failure is
  not, and almost always means no `cargo` on `PATH`.
- A hybrid keystore (sr25519 + FN-DSA-512). The funded test fixture generates
  one on demand; for a manual solve, `load_or_generate_keystore(path)` creates a
  `0600` seed file. A keystore written under the old H3 suite is worthless: the
  format check rejects it, and H4 derives a different account id from the same
  master seed, so the balance does not carry over either. Generate a fresh one
  and fund that.

## Option A -- local DevNet

The DevNet is the fast, offline way to exercise the full propose -> discover ->
solve -> submit path without a public chain. It runs the same node image the
public stack runs, so a spec-matched DevNet is a good pre-flight before an
aglais run.

There are no pinned `:v0.2` image tags any more. `make localdev` resolves the
newest published tag per image at run time (`scripts/newest-tags.py` writes
`data/localdev.tags.env`), because CI does not move `latest` for rc builds and
tracking `latest` would silently hold localdev on an old build. The public
stack selects images by `CHANNEL` instead, defaulting to `beta`; `stable` and
`beta` both run aglais and differ only in how far ahead of the release line
they sit. A `QUIP_*_TAG` pinned in `.env` passes through either path untouched.

Sibling repos under `~/code/gitlab.com/quip.network/`:

- `nodes.quip.network` -- DevNet orchestration (compose, Makefile, coordinator
  invocation); this is where you run `make localdev`.
- `quip-miner` -- the mining stack: the `quip-coordinator`, `quip-miner-exec`
  and `quip-mock-miner` crates. Only needed to read miner internals, such as
  whether a miner registers itself as a mempool solver. Renamed from
  `quip-protocol`, and no longer a Python image source; it is a Rust workspace
  as of the v0.3 line.
- `quip-validator` -- the substrate node (runtime, pallets) + the `quip_signer`
  binding. Renamed from `quip-protocol-rs`.

### Bring-up

Prerequisites: Docker Desktop running; `nodes.quip.network` on the latest
`main`. The `quip.network` GitLab container registry is public, so the
`make localdev` image pulls resolve anonymously: `docker login` is NOT
required (logging in only raises registry rate limits). If you do want to
log in, use `docker login registry.gitlab.com` with a `read_registry` PAT.

From `nodes.quip.network`:

    cp env.example .env          # then set PUID=0 / PGID=0 (see macOS notes)
    make localdev                # wipe + pull + seed topology + bring up the stack

`make localdev` is idempotent and destructive by design: it tears down, wipes
the chain, resolves and pulls the newest published image tags, brings up the
validator + faucet, seeds the `advantage2_system1` topology + difficulty via
`//Alice` sudo, then starts the miner. Seeding now lives inside the coordinator
binary (`quip-coordinator seed-chain`); the old
`scripts/seed-advantage2-topology.py` is gone. Success prints
`localdev stack up` with the dashboard / RPC / faucet URLs. The miner
self-bootstraps: it faucet-funds its account, registers as a PoW miner, and
auto-registers as a mempool solver (`mempool solver guard: registered (cpu)`).
Bootstrap completes ~60-90s after the miner container starts.

**macOS (Apple Silicon):** set `PUID=0` and `PGID=0` in `.env`. Do NOT use
`PGID=$(id -g)` -- on macOS that is `20` (`staff`), which collides with the
miner image's GID `20` (`dialout`) and crash-loops the entrypoint's group
remap. The miner and faucet images are amd64-only and run under emulation, so
the first pull is slow and CPU is higher than native -- expected, not a hang.

### Verify the stack

- Blocks + runtime version: read `state_getRuntimeVersion` over the RPC and
  record what it says. Nothing is pinned here on purpose -- localdev tracks the
  newest published image, so its runtime moves whenever one is published, and a
  figure written down in this guide would be stale within the week. aglais was
  on `specVersion 117` / `transactionVersion 7` on 2026-09-15; a localdev stack
  brought up from current images should be at or ahead of that.
- Containers: `docker ps` -- all `*-localdev` Up, `quip-cpu-localdev` not
  Restarting.
- Miner healthy: `docker logs quip-cpu-localdev | grep -iE "solver guard: registered|topology .from chain.|mempool=on"`
  -- expect the solver registered, the DevNet `DefaultTopology` hash, and
  mempool on. `make localdev` seeds the coordinator's built-in
  `advantage2-system1` preset (`allowed_h = [-1000, 0, 1000]`, hashing to
  `0xfb91813b...`), which is a different topology from the `allowed_h = [0]`
  one aglais runs. `SolverQuip` reads whichever the chain reports, so nothing
  in xquad needs to know which you are on -- but never carry a hash across.

### Host access

The stack exposes everything through Caddy on `:20049`:

- RPC: `ws://localhost:20049/rpc` (use `http://localhost:20049/rpc` for HTTP).
- Faucet: base `http://localhost:20049/api/faucet` (the suite POSTs
  `.../request`).

If you prefer raw ports, add a `ports:` mapping for `quip-validator`
(`9944:9944`) and `quip-faucet` (`8087:8087`) -- the localdev stack does not
publish them by default. Note that `docker-compose.override.yml` is no longer
the file to put that in: the localdev overrides moved to the explicit
`docker-compose.localdev.yml`, which plain `docker compose` does not auto-load,
so use a differently-named override and pass it with `-f`.

### Run the suite

    make test-quip \
        QUIP_RPC_URL=ws://localhost:20049/rpc \
        QUIP_FAUCET_URL=http://localhost:20049/api/faucet

A fresh order solves in ~2-4 blocks at localdev block time (~14-21s propose to
on-chain solution under emulation).

### Teardown

From `nodes.quip.network`: `make down` (stop), `make clean-chain` (wipe chain
only), `make clean` (full reset). Re-run `make localdev` after any wipe -- state
is non-persistent and order ids reset, so never hard-code one.

### Troubleshooting

- **Miner crash-loops "at least one validator URL is required":** the persisted
  `data/config.toml` has an explicit empty `validators = []` (old-schema
  leftover) that defeats the built-in fallback. On latest `main`,
  `make localdev` overwrites the config; if you hand-edited it, delete the
  `validators` line or set `validators = ["ws://quip-validator:9944"]`.
- **Stale checkout of `nodes.quip.network`:** a checkout still on the `v0.2`
  line does not merely lag, it fails. The miner images moved to a separate
  `v0.3` repository line, `data/config.toml` changed schema (`make updateconfig`
  migrates it), and chain seeding moved into the coordinator. Pull `main` before
  blaming the stack.
- **0 solutions with a healthy-looking miner:** confirm the node's runtime with
  `state_getRuntimeVersion` and that the miner logs the `mempool_producer` /
  `work_scheduler` stack. `make localdev` resolves the newest published tags on
  every run, so a stale image is far less likely than it used to be; a stale
  `.env` pinning `QUIP_*_TAG` is the remaining way to get one.
- **macOS miner crash-loop before any app logs:** `PGID=20` group collision --
  set `PUID=0` / `PGID=0`.
- **Faucet:** on a fresh bring-up the miner's first requests hit
  `Connection refused` for ~30s until the faucet is up, then fund on retry
  (expected). A repeat request for the same account returns HTTP 429
  (rate-limited); use a fresh keystore per test if you hit the limit.

## Option B -- the public aglais network

aglais is a real, multi-node deployment with a registered solver fleet. You do
not control the solvers or the topology -- you propose jobs and a third-party
fleet solves them. It runs the H4 hybrid suite (sr25519 + FN-DSA-512), which is
what the signing layer speaks.

### Coordinates (verify before a run -- these move)

- **RPC:** the `aglais` preset in `xqsa/quip_networks.py` is the source of
  truth; it points at bootnode-1's validator RPC (bootnode-2 and bootnode-3
  serve the same chain). The commands below spell out its current RPC and
  faucet values; if they disagree with the preset, the preset wins. Any node can fall behind the chain tip and serve a stale,
  frozen view -- balances read as 0 and freshly submitted extrinsics look
  like they never land, even though the chain is live. Confirm the node is
  caught up before trusting reads (see the liveness check below), and point
  `QUIP_RPC_URL` at a different RPC if the one you are on is not syncing.
- **Faucet:** the `aglais` preset's faucet coordinate in
  `xqsa/quip_networks.py` is the source of truth, healthy as of 2026-09-15
  (`/health` returns `{"status":"ok"}`; the bare root returns 404, which is not a
  fault). Set `QUIP_FAUCET_URL` to it and the funded tier runs. A repeat request
  for the same account is rate-limited, so use a fresh keystore per run.
- **Block time:** ~6s.
- **Runtime:** advances over time (`specVersion 117` / `transactionVersion 7`,
  read live on 2026-09-15). ALWAYS re-check with `state_getRuntimeVersion` before
  assuming a pinned value. The signer reads `transactionVersion` from chain
  metadata dynamically, so a runtime bump does not by itself break submission --
  but confirm rather than assume.
- **Metadata:** the runtime serves Metadata V16, which `scalecodec` cannot
  decode. Build clients through `xqsa.quip_metadata.connect`, which pulls V14
  from the versioned runtime API; a stock `SubstrateInterface` fails with
  `Index '16' not present in Enum type mapping` (see the gotcha below).
- **Spec id:** `DEFAULT_ISING_SPEC_ID = 0x8f46f3a3...` (== chain
  `DefaultIsingSpecId`); `MinReward` = 1e12 planck (1 UNIT).

Quick version + liveness check. A synced node reports `isSyncing == false` and
`currentBlock == highestBlock`; if it is behind, its state reads are stale:

    uv run --extra quip python -c "from xqsa.quip_metadata import connect; \
        from xqsa.quip_networks import NETWORKS; \
        s=connect(NETWORKS['aglais'].rpc); \
        print('runtime', s.rpc_request('state_getRuntimeVersion',[])['result']); \
        print('sync   ', s.rpc_request('system_syncState',[])['result']); \
        print('health ', s.rpc_request('system_health',[])['result'])"

### Topology

The same `advantage2_system1` graph hashes differently per deployment (each
network's allowed-value specs fold into the hash), so `SolverQuip` resolves the
topology at construction from `topology=`, then `QUIP_TOPOLOGY`, then chain
`QuantumPow.DefaultTopology`. There is no pinned fallback in the codebase, and a
topology that resolves from none of the three raises rather than selecting a
hash no chain would accept. On aglais `DefaultTopology` is
`0xcbec1eb4...` over 4577 nodes / 41514 edges, read live on 2026-09-15. Never
assume the DevNet hash on aglais or vice versa.

`solve()` does not consult `MineableTopologies`. That set is the chain's active
mining set: it gates `submit_proof`, and so block production, not the compute
mempool. Nothing binds an order to a topology at `propose_job` -- an order
carries its nodes, edges and coefficients inline and no topology hash at all --
so the chain cannot perceive which topology an order was built against, and
mineability cannot affect whether an order is admitted or answered.

To exercise that, aglais carries a permanently registered non-mineable topology:
a complete graph on 16 nodes (K16), hash
`0x830abc16c0b28b26f119f3a1279d07811bdeb9c0234d7f442ae46d6698d9a797`, registered
at block 232673. It is registered, not mineable, and not the default. Point
`QUIP_TOPOLOGY` at it to run the whole suite against it:

    QUIP_MINER_PROBE_TIMEOUT=180 \
      QUIP_TOPOLOGY=0x830abc16c0b28b26f119f3a1279d07811bdeb9c0234d7f442ae46d6698d9a797 \
      make test-quip \
        QUIP_RPC_URL=wss://bootnode-1.aglais.quip.network:20049/rpc \
        QUIP_FAUCET_URL=https://faucet.aglais.quip.network

Reuse K16; do not register another. There is no `unregister_topology`, so every
registration is permanent.

### Run the suite

    QUIP_MINER_PROBE_TIMEOUT=180 \
      make test-quip \
        QUIP_RPC_URL=wss://bootnode-1.aglais.quip.network:20049/rpc \
        QUIP_FAUCET_URL=https://faucet.aglais.quip.network

The submit-path tests always run; the `TestEndToEnd` cases are gated behind the
`solving_miner` probe. If the probe skips (no solution within the window), retry
once when the fleet is active.

### Learnings / gotchas

- **Discovery gap (important):** the fleet discovers jobs ONLY from live
  `JobProposed` events -- there is NO storage backfill of pre-existing open
  orders. An order proposed BEFORE a solver subscribed is invisible to it
  forever. Always test with a freshly proposed order; a fresh one solves within
  ~1-2 blocks while the fleet is live, but historical orders that predate the
  current fleet stay unsolved, which looks like "nothing solves" if you only
  inspect old orders. (Known miner-side follow-up, not a client bug.)
- **Runtime can advance under you:** re-read `state_getRuntimeVersion` each
  session; do not hard-code pinned facts. The signer adapts `transactionVersion`
  from metadata, so submission keeps working across bumps -- but re-validate
  after a known upgrade.
- **Metadata V16 blocks a stock client:** `substrate-interface` decodes through
  `scalecodec`, whose `MetadataAll` enum ends at V14, so
  `SubstrateInterface.init_runtime()` raises `ValueError: Index '16' not present
  in Enum type mapping` against any runtime from `specVersion 116` on. No
  release of either library decodes V16. `xqsa.quip_metadata` fetches V14
  through `state_call("Metadata_metadata_at_version", 14)` instead, and
  `SolverQuip` builds its client that way; ad-hoc scripts must do the same. The
  shim falls back to `state_getMetadata` for older DevNet images, and raises
  `QuipMetadataError` naming the served version when neither path decodes.
- **Watch for a lagging RPC node:** a node that has fallen behind the chain tip
  serves a frozen snapshot, and every read then looks wrong -- balances at 0,
  funded accounts "missing", submitted extrinsics that never land -- while the
  chain is actually fine. A faucet request returning `200 + extrinsic_hash` is NOT
  proof of funding. Check `isSyncing == false` and `currentBlock == highestBlock`;
  if a node is not syncing, point `QUIP_RPC_URL` at a different RPC URL.
- **Lifecycle:** an order's effective expiry is `created_at + deadline_blocks`,
  tightened to `first_solution_at + block_wait` once a first solution lands. An
  order that expires with zero solutions can be auto-reclaimed (SolverQuip does
  this and raises a job-failed error, releasing the reserved reward).
- **Fleet answers can be valid but suboptimal:** the aglais fleet runs
  heuristic GPU solvers and may return a correct, chain-consistent assignment
  that is not the global optimum -- observed: a 3-var binary model whose true
  optimum is -2 came back as -1, while the controlled DevNet CPU fleet returns
  -2 deterministically. Do not assert global optimality against a live fleet.
  `TestEndToEnd` asserts only pipeline correctness (`energy_matches_chain`, a
  returned solution, domain feasibility); encoding and optimality correctness
  are guarded deterministically offline by
  `test_quip.py::test_encoded_problem_argmin_decodes_to_optimum`.
- **Direct `pytest` invocation:** export `QUIP_RPC_URL` / `QUIP_FAUCET_URL` as
  environment variables before the command. Passing them as trailing `KEY=VAL`
  args is a make-ism (`make VAR=val`); pytest reads trailing `KEY=VAL` tokens
  as file paths and errors with "file or directory not found".

## Manual smoke solve (optional)

Beyond the pytest suite, a single small model round-trips the propose + decode +
energy-canary path. Set `QUIP_KEYSTORE` and fund the keystore, then:

    export QUIP_KEYSTORE=/tmp/quip-ks.json
    uv run --extra quip python - <<'PY'
    from xqsa.quip import SolverQuip
    from xqvm_py.xqmx import XQMX
    m = XQMX.spin_model(2)
    m.set_linear(0, 1); m.set_quadratic(0, 1, -1)   # brute-force optimum = -2
    r = SolverQuip.for_network("aglais").solve(m)   # or "devnet"
    print("energy:", r.energy, "| matches chain:", r.metadata["energy_matches_chain"])
    PY

Expect `energy_matches_chain: True`; energy is typically `-2.0`, though a
heuristic fleet may return a valid but suboptimal value. Get the account id to
fund via
`load_or_generate_keystore('/tmp/quip-ks.json').account_id.hex()` and POST
`{"dest":"0x<accountid>","amount":10000000000000}` to the faucet's `/request`
endpoint.

## Inspecting chain state

Independent confirmation lives in `QuantumComputeMempool` storage: `JobOrders`
(status / solution_count / first_solution_at), `OrderSolutions`,
`OrderFrontRunner` (winning solver + `energy_milli`, the natural energy x1000),
`OrderResults` (populated at finalization), and `Solvers` (each with
`solutions_submitted`). Topology lives under
`QuantumPow.{DefaultTopology,MineableTopologies,RegisteredTopologies}`. Strip
the large `ising_params` arrays when dumping orders.

## Which to use when

- **DevNet** for fast, deterministic, offline iteration and as a spec-matched
  pre-flight before an aglais run. You control the fleet, so a fresh order
  always solves quickly.
- **aglais** for the real-solver, real-timing, real-runtime validation the
  DevNet cannot reproduce -- run it before landing a SolverQuip change that
  touches submission, decoding, or the lifecycle. It is the only public target;
  the older TestNet is retired.
