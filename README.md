# Stop-Loss Guardian — Penjaga Stop-Loss Agentik

**Reactive Omni Buildathon** (DoraHacks) — track **Reactive Meets AI**

> An AI arms the stop-loss; the Reactive Network fires it — a cross-chain, keeperless
> stop-loss that cannot fall asleep.

## The problem

A stop-loss is only as reliable as whatever watches the price. In practice that is a
centralized keeper bot: it must stay online, it holds keys or exchange credentials, and
if it crashes, sleeps, or gets rate-limited at the exact moment the market breaks, the
position bleeds. The trigger lives off-chain, so nothing guarantees the exit ever happens.

## What we built

This project moves the trigger on-chain and puts an AI inside the loop:

1. **AI signal service (off-chain, `signal-service/`).** A deterministic signal model
   watches the price feed and scores risk from two measurements — drawdown from the
   recent peak and short-window volatility. When drawdown ≥ 8% or volatility ≥ 3%, it
   decides to ARM a stop-loss and computes the trigger price with a volatility-scaled
   safety buffer (`buffer = clamp(2 × volatility, 5%, 25%)`); it can also trail the
   trigger as risk evolves, or disarm when the market calms. The decision is written
   on-chain as an **EIP-712 signed update** (`updateSignalSigned`), simulated against
   the live registry before anything is broadcast.
2. **AISignalRegistry (Base Sepolia).** Holds the AI's armed trigger per token. The
   owner can only rotate the operator — nobody can forge a signal.
3. **StopLossGuardian (Reactive Network / Lasna).** A reactive contract (Reactive Omni
   pattern) that subscribes to `PriceUpdated` and `SignalUpdated` on Base Sepolia,
   mirrors the AI's parameters from the logs, and — the moment price ≤ trigger —
   requests a callback via the system contract (`requestCallbackV_1_0`). A per-arming
   latch prevents double-firing.
4. **StopLossExecutor + MockPositionVault (Base Sepolia).** The callback lands through
   the chain's callback proxy with the double guard (`onlyServiceProvider` +
   `onlyCallbackSender`) and exits every protected position in the vault.

No keeper. No human. Once the AI arms the stop, the network itself guarantees the exit.

## How it works

```
AI signal service ──EIP-712 signed update──▶ AISignalRegistry (Base Sepolia)
                                                        │ SignalUpdated
MockPriceFeed ──PriceUpdated──▶ StopLossGuardian (Lasna, AbstractReactive)
                                                        │ price <= trigger
                                                        ▼ requestCallbackV_1_0
                              StopLossExecutor (Base Sepolia, AbstractCallback)
                                                        │ exitAllForToken
                                                        ▼
                              MockPositionVault — positions closed
```

## Repository layout

- `src/interfaces/IAISignal.sol` — signal interface the AI writes to.
- `src/origin/` — `AISignalRegistry` (operator + signed updates), `MockPriceFeed`.
- `src/reactive/` — `StopLossGuardian` (subscriptions, trigger logic, callback requests).
- `src/destination/` — `StopLossExecutor` (authenticated callback), `MockPositionVault`.
- `lib/reactive-lib-omni/` — official Reactive Omni library, vendored unmodified.
- `lib/forge-std/` — Foundry standard library, vendored.
- `test/` — Foundry tests; the system contract is replaced by a recording mock etched
  at `0x8888…8888`, so the full guardian logic runs locally with no network.
- `signal-service/` — the off-chain AI signal service (Python 3, stdlib only, with
  hand-rolled Keccak-256 / secp256k1 / EIP-712). See `signal-service/README.md`.

## Deployed contracts (testnet)

All five contracts are **Sourcify exact-match verified** (chains 84532 and 5318007).

| Contract | Chain | Address | Deploy tx |
|---|---|---|---|
| MockPriceFeed | Base Sepolia (84532) | `0x0Bd0562b22041c4E444738326b24936D9fBA3c10` | `0x7db67cfab4a499d8467adf193414ffecbf1159bb0f1abea7b9a5e45a40067586` |
| AISignalRegistry | Base Sepolia (84532) | `0xE2C38a9D4fb88038775bc1ca00bB920CD0a416a8` | `0x729d521793e45e98c78226309fb99b0a3a31be082baaa47990f04855b2fa891a` |
| StopLossGuardian | Lasna (5318007) | `0x0Bd0562b22041c4E444738326b24936D9fBA3c10` | `0x93b797bc33fd679f6232974f73f4b84d1e460ed88f7e1801bb0128e5502aad6e` |
| MockPositionVault | Base Sepolia (84532) | `0x0e7Ea8e0A31AF134c85bCFc6F541a57FaB816335` | `0xbc3d97b45a971a0172700e7b906bcee233d0514153a1c6375a35e6b369dce4fb` |
| StopLossExecutor | Base Sepolia (84532) | `0xC8Caf8fA722DC6cd9F08C2CE44fe179b66F716D2` | `0x2792be71554b35991a0c85e48e879b7071ce166601e354cf865fda7b039bc4d0` |

## Proof — the full loop, already executed on testnet

Demo token `0x0000000000000000000000000000000000007e57`, trigger 90, break price 85,
position 1000 units:

| # | Step | Chain | Tx hash |
|---|---|---|---|
| 1 | Position opened in the vault | Base Sepolia | `0xfe4ae909d4af11f819a9aabc84a7301d4937d8253991564d51d4512a14714c3e` |
| 2 | AI arms the stop-loss (`updateSignal`) | Base Sepolia | `0x6e343423b89906aed1ada288c8124a94038b62882f44b284a87467f2a4b23e10` |
| 2b | Guardian mirrors the arm (`GuardianArmed`) | Lasna | `0x277f6299130139cbe5b68bfe64b8e4ff287e8228b5be4b57bac6083385f158d8` |
| 3 | Price breaks (`pushPrice` → 85) | Base Sepolia | `0x321edf6017dfb693fe2470118d098733cc1133920cbb5469d6547a981a134b84` |
| 4 | Guardian fires (`StopLossTriggered` + callback request) | Lasna | `0x324530ff68c158027bfb92db05d1ce6e5f9517209fcb91c5cbbe79e82cdf587c` |
| 5 | Callback delivered → position exited (`StopLossExecuted`) | Base Sepolia | `0x80f649b97028e004d76cdf37b3c7e31fcafa90f7af8e4e33909787b0c36350ec` |

After step 5, `vault.positions(owner, token)` reads **open = false** — the position
actually closed, end to end, with no human or keeper in the loop. To verify: replay the
hashes on the Base Sepolia explorer (sepolia.basescan.org) and the Lasna explorer; the
Lasna firing tx targets the Reactive system contract
`0x8888888888888888888888888888888888888888`, and the Base callback arrives through the
callback proxy `0x476F44b11F5C942c58f4c8E3d0300E5710743585`.

## Run the contract tests

Requires [Foundry](https://book.getfoundry.sh/). Dependencies are vendored under
`lib/`, so no network fetch is needed:

```bash
forge build
forge test        # 22/22 passing
```

The suite covers double-guard callback authentication, anti-replay on signed signals,
and the anti-double-fire latch.

## Run the AI signal service

Python 3, standard library only. From `signal-service/`:

```bash
# Dry-run polling loop (default — reads the chain, prints decisions, broadcasts nothing)
python3 run.py

# One poll, then exit (dry-run)
python3 run.py --once

# Offline unit tests (26 tests, no RPC needed)
python3 -m unittest discover -s tests -v
```

Live broadcasting (`--live`) is testnet-only and requires an operator key file:
`config.json` points `key_file` at a local JSON key file (a `{"privateKey": ...}` file
that must **never** be committed — it is excluded by `.gitignore`). Point it at your
own testnet operator key before using `--live`. Every update is `eth_call`-simulated
against the deployed registry before any broadcast.

## Tech stack

Solidity 0.8.30 (Foundry), Reactive Omni pattern (reactive-lib-omni), EIP-712 signed
signal relay, Python 3 (stdlib-only) off-chain signal service with hand-rolled
Keccak-256/secp256k1. Built during the event window on testnet (Base Sepolia + Lasna).

## License

MIT — see `LICENSE`. Vendored libraries under `lib/` keep their own licenses.
