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
- the public **TestNet** (LiveNet) -- a real multi-node deployment with a
  third-party GPU solver fleet, a real faucet, and a rolling runtime. This is
  the target for validation the DevNet cannot fully stand in for (real solvers,
  real timing, real runtime upgrades).

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
| `QUIP_RPC_URL` | required for e2e | chain RPC, `ws://` (DevNet) or `wss://` (TestNet). `make test-quip` / `make test-quip-e2e` pass it through explicitly. |
| `QUIP_FAUCET_URL` | optional | faucet **base** URL; the funded fixture POSTs to `<QUIP_FAUCET_URL>/request`. Without it only the read-only connectivity tests run. |
| `QUIP_MINER_PROBE_TIMEOUT` | optional (default 60) | how long the `solving_miner` probe waits before skipping the end-to-end tier. Raise it on a slow or remote fleet. |
| `SSL_CERT_FILE` | macOS + `wss://` only | CA bundle for the TLS handshake (see [macOS TLS](#macos-tls-wss-only)). |

Any variable exported in your shell -- or passed as `make VAR=val` -- reaches
pytest, so you can set `SSL_CERT_FILE` and `QUIP_MINER_PROBE_TIMEOUT` alongside
the two the recipe forwards.

### The two test tiers

- **submit + lifecycle** (always run against any healthy chain): connectivity,
  the live `propose_job` SCALE / extras contract (a clean submission with no
  `System.ExtrinsicFailed`), the topology fetch, the mineability pre-check
  (`MineableTopologies` membership), the balance pre-check, the timeout path,
  and expired-no-solution auto-reclaim. These need no returned solution.
- **end-to-end** (`TestEndToEnd`): need the fleet to actually solve. They are
  guarded by the `solving_miner` fixture, which proposes a throwaway order and
  waits for a solution; it skips the tier cleanly when no fleet is active. A
  fresh order solves in ~1-2 blocks when a fleet is running.

## Common prerequisites

- `uv sync --extra quip` in this repo (installs `substrate-interface` + the
  `quip-signer` wheel; on macOS the signer sdist builds from Rust in ~15s). The
  make targets do this for you via `uv run --extra quip`.
- A hybrid keystore (sr25519 + ML-DSA-44). The funded test fixture generates one
  on demand; for a manual solve, `load_or_generate_keystore(path)` creates a
  `0600` seed file.

### macOS TLS (`wss://` only)

The python.org / uv Python build ships no system CA bundle, so `wss://`
handshakes fail on macOS unless you point OpenSSL at certifi's bundle:

    export SSL_CERT_FILE=$(uv run --extra quip python -m certifi)

Export it for every command that touches the TestNet (REPL, tests, scripts). It
is harmless on Linux and unnecessary for the `ws://` DevNet.

## Option A -- local DevNet

The DevNet is the fast, offline way to exercise the full propose -> discover ->
solve -> submit path without the public TestNet. It runs the identical `:v0.2`
node image the TestNet runs, so a spec-matched DevNet is a good pre-flight
before a TestNet run.

Sibling repos under `~/code/gitlab.com/quip.network/`:

- `nodes.quip.network` -- DevNet orchestration (compose, Makefile, seed script);
  this is where you run `make localdev`.
- `quip-protocol` -- Python miner image source (only needed to read miner
  internals).
- `quip-protocol-rs` -- the substrate node (runtime, pallets) + the `quip_signer`
  binding.

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
the chain, force-pulls the current `:v0.2` images, brings up the validator +
faucet, seeds the `advantage2_system1` topology (4577 nodes / 41515 edges) +
difficulty via `//Alice` sudo, then starts the miner. Success prints
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

- Blocks + runtime version: `state_getRuntimeVersion` over the RPC should report
  `specVersion 112`, `transactionVersion 5` (matches the TestNet).
- Containers: `docker ps` -- all `*-localdev` Up, `quip-cpu-localdev` not
  Restarting.
- Miner healthy: `docker logs quip-cpu-localdev | grep -iE "solver guard: registered|topology .from chain.|mempool=on"`
  -- expect the solver registered, the DevNet `DefaultTopology` hash
  (`0xfb91813b...`, deployment-specific), and mempool on.

### Host access

The stack exposes everything through Caddy on `:20049`:

- RPC: `ws://localhost:20049/rpc` (use `http://localhost:20049/rpc` for HTTP).
- Faucet: base `http://localhost:20049/api/faucet` (the suite POSTs
  `.../request`).

If you prefer raw ports, add a `ports:` mapping for `quip-validator`
(`9944:9944`) and `quip-faucet` (`8087:8087`) in a personal
`docker-compose.override.yml` -- the localdev stack does not publish them by
default.

### Run the suite

    make test-quip \
        QUIP_RPC_URL=ws://localhost:20049/rpc \
        QUIP_FAUCET_URL=http://localhost:20049/api/faucet

No `SSL_CERT_FILE` is needed for the `ws://` DevNet. A fresh order solves in
~2-4 blocks at localdev block time (~14-21s propose to on-chain solution under
emulation).

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
- **"Works differently than the TestNet" / 0 solutions with a healthy-looking
  miner:** you are almost certainly on a STALE cached image. `:v0.2` is a
  rolling tag; an old cache can be a pre-migration node and/or a pre-T7 miner
  that behaves nothing like the current fleet. `make localdev` force-pulls;
  confirm the running node reports `specVersion 112` and the miner logs the
  `mempool_producer` / `work_scheduler` stack.
- **macOS miner crash-loop before any app logs:** `PGID=20` group collision --
  set `PUID=0` / `PGID=0`.
- **Faucet:** on a fresh bring-up the miner's first requests hit
  `Connection refused` for ~30s until the faucet is up, then fund on retry
  (expected). A repeat request for the same account returns HTTP 429
  (rate-limited); use a fresh keystore per test if you hit the limit.

## Option B -- public TestNet

The public TestNet is a real, multi-node deployment with a registered solver
fleet (~20 GPU solvers). You do not control the solvers or the topology -- you
propose jobs and a third-party fleet solves them.

### Coordinates (verify before a run -- these move)

- **RPC:** use a validator node, e.g. `wss://bootnode-1.testnet.quip.network:20049/rpc`.
  Any node can fall behind the chain tip and serve a stale, frozen view -- balances
  read as 0 and freshly submitted extrinsics look like they never land, even though
  the chain is live. Confirm the node is caught up before trusting reads (see the
  liveness check below), and point `QUIP_RPC_URL` at a different RPC if the one you
  are on is not syncing.
- **Faucet:** base `https://faucet.testnet.quip.network` (the suite POSTs
  `.../request`). One dispense per account; rate-limited.
- **Block time:** ~6s.
- **Runtime:** advances over time (as of writing, `specVersion 112` /
  `transactionVersion 5`). ALWAYS re-check with `state_getRuntimeVersion` before
  assuming a pinned value. The signer reads `transactionVersion` from chain
  metadata dynamically, so a runtime bump does not by itself break submission --
  but confirm rather than assume.
- **Spec id:** `DEFAULT_ISING_SPEC_ID = 0x8f46f3a3...` (== chain
  `DefaultIsingSpecId`); `MinReward` = 1e12 planck (1 UNIT).

Quick version + liveness check. A synced node reports `isSyncing == false` and
`currentBlock == highestBlock`; if it is behind, its state reads are stale:

    CERT=$(uv run --extra quip python -m certifi)
    RPC=wss://bootnode-1.testnet.quip.network:20049/rpc
    SSL_CERT_FILE="$CERT" uv run --extra quip python -c "import substrateinterface as si; \
        s=si.SubstrateInterface(url='$RPC'); \
        print('runtime', s.rpc_request('state_getRuntimeVersion',[])['result']); \
        print('sync   ', s.rpc_request('system_syncState',[])['result']); \
        print('health ', s.rpc_request('system_health',[])['result'])"

### Topology

The same `advantage2_system1` graph hashes differently per deployment (each
network's allowed-value specs fold into the hash), so `SolverQuip` resolves the
topology from chain `QuantumPow.DefaultTopology` at construction -- the pinned
`ADVANTAGE2_SYSTEM1_TOPOLOGY_HASH` is only a fallback, and `topology=` overrides.
On the TestNet `DefaultTopology` is `0xe66d3dfa...` and is the sole
`MineableTopologies` entry, so jobs against it are eligible to be mined. Never
assume the DevNet hash on the TestNet or vice versa. `solve()` pre-validates that
the resolved hash is in `MineableTopologies` before reserving the reward (raising
`QuipTopologyError`). Only a runtime that lacks the `MineableTopologies` storage
item entirely skips the check; a present-but-empty set rejects.

### Run the suite

    CERT=$(uv run --extra quip python -m certifi)
    SSL_CERT_FILE="$CERT" QUIP_MINER_PROBE_TIMEOUT=180 \
      make test-quip \
        QUIP_RPC_URL=wss://bootnode-1.testnet.quip.network:20049/rpc \
        QUIP_FAUCET_URL=https://faucet.testnet.quip.network

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
- **Fleet answers can be valid but suboptimal:** the TestNet fleet runs
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
energy-canary path. Export the same env you used above (for the TestNet also
export `SSL_CERT_FILE` and set `QUIP_RPC_URL`/`QUIP_KEYSTORE`), fund a keystore,
then:

    export QUIP_RPC_URL=ws://localhost:20049/rpc     # or wss://bootnode-1.testnet.quip.network:20049/rpc (TestNet)
    export QUIP_KEYSTORE=/tmp/quip-ks.json
    uv run --extra quip python - <<'PY'
    from xqsa.quip import SolverQuip
    from xqvm_py.xqmx import XQMX
    m = XQMX.spin_model(2)
    m.set_linear(0, 1); m.set_quadratic(0, 1, -1)   # brute-force optimum = -2
    r = SolverQuip().solve(m)
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
  pre-flight before a TestNet run. You control the fleet, so a fresh order
  always solves quickly.
- **TestNet** for the real-solver, real-timing, real-runtime validation the
  DevNet cannot reproduce -- run it before landing a SolverQuip change that
  touches submission, decoding, or the lifecycle.
