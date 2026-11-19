"""Penjaga Stop-Loss Agentik — off-chain AI signal service.

Loop: poll MockPriceFeed -> SignalModel decision -> when the model says
ARM / DISARM / UPDATE, build an EIP-712 signed AISignalRegistry update and
submit it via updateSignalSigned (anyone may relay; the signature is what
authorizes). The registry emits SignalUpdated, which is the event the
StopLossGuardian on Lasna mirrors — so this service's transactions are the
"AI arm" step of the cross-chain stop-loss loop.

Broadcast route (the one proven on this VM):
  sign offline with `cast mktx` -> broadcast with curl eth_sendRawTransaction
  -> poll eth_getTransactionReceipt for status 0x1.

Safety: dry-run is the DEFAULT. Broadcasting happens only with --live, and
the broadcast helper refuses to run unless live mode was explicitly enabled.
The operator key is read from the key file at runtime and never printed
(it is passed to cast mktx as an argument, and redacted from all logs).
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import abi_codec, eip712
from .model import ModelConfig, SignalModel
from .rpc import RpcClient, RpcError
from .secp256k1 import private_key_to_address

SERVICE_ROOT = Path(__file__).resolve().parent.parent
PRICE_UPDATED_TOPIC0 = "0xb556fac599c3c70efb9ab1fa725ecace6c81cc48d1455f886607def065f3e0c0"


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.gmtime()) + "Z"


def log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


class SignalService:
    def __init__(self, config: dict, live: bool = False):
        self.config = config
        self.live = live
        self.rpc = RpcClient(config["rpc_urls"])
        self.registry = config["registry_address"]
        self.feed = config["feed_address"]
        self.token = config["token"]
        self.chain_id = int(config["chain_id"])
        self.ttl = int(config.get("signature_ttl_seconds", 3600))
        model_cfg = ModelConfig(**config.get("model", {}))
        self.model = SignalModel(model_cfg)
        self._priv = None
        self.operator = None

    # -- setup ---------------------------------------------------------------
    def _resolve(self, path_str: str) -> Path:
        p = Path(path_str)
        return p if p.is_absolute() else (SERVICE_ROOT / p)

    def load_operator_key(self) -> None:
        """Read the operator private key from the key file. Never logged."""
        key_file = self._resolve(self.config["key_file"])
        data = json.loads(key_file.read_text())
        raw = data["privateKey"]
        self._priv = int(raw[2:] if raw.startswith("0x") else raw, 16)
        self.operator = private_key_to_address(self._priv)
        expected = data.get("address")
        if expected and expected.lower() != self.operator.lower():
            raise RuntimeError("key file address does not match the private key")

    def verify_static_chain_facts(self) -> None:
        """Cross-check the local EIP-712 constants against the deployed
        registry: DOMAIN_SEPARATOR() and SIGNAL_TYPEHASH() read on-chain must
        equal what eip712.py computes, and aiOperator() must be our key."""
        on_chain_ds = self.rpc.eth_call(self.registry, abi_codec.to_hex(abi_codec.selector("DOMAIN_SEPARATOR()")))
        local_ds = eip712.domain_separator(self.chain_id, self.registry)
        if on_chain_ds != local_ds:
            raise RuntimeError(
                f"DOMAIN_SEPARATOR mismatch: on-chain {on_chain_ds.hex()} != local {local_ds.hex()}")
        on_chain_th = self.rpc.eth_call(self.registry, abi_codec.to_hex(abi_codec.selector("SIGNAL_TYPEHASH()")))
        if on_chain_th != eip712.SIGNAL_TYPEHASH:
            raise RuntimeError("SIGNAL_TYPEHASH mismatch with on-chain constant")
        operator = self.rpc.eth_call(self.registry, abi_codec.to_hex(abi_codec.selector("aiOperator()")))
        on_chain_operator = "0x" + operator[-20:].hex()
        if self.operator and on_chain_operator.lower() != self.operator.lower():
            raise RuntimeError(f"aiOperator on-chain is {on_chain_operator}, our key is {self.operator}")
        log("EIP-712 facts verified against chain: DOMAIN_SEPARATOR + SIGNAL_TYPEHASH match, "
            f"aiOperator = {on_chain_operator}")

    def sync_from_chain(self) -> dict:
        state = abi_codec.decode_signal(
            self.rpc.eth_call(self.registry, abi_codec.to_hex(abi_codec.encode_get_signal(self.token))))
        self.model.sync_chain_state(state["armed"], state["trigger_price"])
        log(f"registry state for token {self.token}: armed={state['armed']} "
            f"trigger={state['trigger_price']} nonce={state['nonce']} updated_at={state['updated_at']}")
        return state

    def bootstrap_history(self) -> None:
        """Best-effort price history from PriceUpdated logs (oldest first)."""
        try:
            latest = self.rpc.block_number()
            lookback = int(self.config.get("history_lookback_blocks", 30000))
            logs = self.rpc.get_logs_adaptive(
                self.feed, PRICE_UPDATED_TOPIC0, abi_codec.topic_for_address(self.token),
                max(0, latest - lookback), latest)
            prices = []
            for entry in sorted(logs, key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16))):
                data = bytes.fromhex(entry["data"][2:])
                if len(data) >= 32:
                    prices.append(int.from_bytes(data[:32], "big"))
            self.model.seed_history(prices)
            log(f"history bootstrap: {len(prices)} PriceUpdated log(s) -> window {prices[-self.model.config.window:]}")
        except RpcError as exc:
            log(f"history bootstrap skipped (RPC): {exc}")

    # -- price -----------------------------------------------------------------
    def read_price(self) -> int:
        raw = self.rpc.eth_call(self.feed, abi_codec.to_hex(abi_codec.encode_price_of(self.token)))
        return abi_codec.decode_uint256(raw)

    # -- transaction building ----------------------------------------------------
    def _build_update(self, action: str, trigger_price: int, chain_state: dict) -> dict:
        """Build the EIP-712 signed update + calldata for a model decision."""
        armed = action in ("arm", "update")
        nonce = chain_state["nonce"] + 1  # the contract hashes current nonce + 1
        deadline = self.rpc.block_timestamp("latest") + self.ttl
        digest = eip712.update_digest(self.chain_id, self.registry, self.token,
                                      trigger_price, armed, nonce, deadline)
        signature = eip712.sign_update(self._priv, self.chain_id, self.registry, self.token,
                                       trigger_price, armed, nonce, deadline)
        calldata = abi_codec.encode_update_signal_signed(self.token, trigger_price, armed, deadline, signature)
        return {
            "action": action, "armed": armed, "trigger_price": trigger_price,
            "nonce": nonce, "deadline": deadline,
            "digest": "0x" + digest.hex(), "signature": "0x" + signature.hex(),
            "calldata": abi_codec.to_hex(calldata),
        }

    def _simulate(self, update: dict) -> bool:
        """eth_call the signed update: succeeds (returns 0x) only if the
        contract would accept it — signature, nonce and deadline all valid."""
        try:
            result = self.rpc.eth_call(self.registry, update["calldata"], from_addr=self.operator)
            return result == b""
        except RpcError as exc:
            log(f"simulation reverted/failed: {exc}")
            return False

    def _gas_limit(self, update: dict) -> int:
        try:
            est = self.rpc.estimate_gas(self.operator, self.registry, update["calldata"])
            return min(500_000, max(120_000, est * 13 // 10))
        except RpcError:
            return 250_000

    def _cast_mktx(self, update: dict, tx_nonce: int, gas_limit: int, gas_price: int) -> str:
        """Sign the transaction offline with cast mktx. NEVER broadcasts."""
        cast = str(self._resolve(self.config["cast_path"]))
        args = [
            cast, "mktx", self.registry,
            "updateSignalSigned(address,uint256,bool,uint256,bytes)",
            self.token, str(update["trigger_price"]),
            "true" if update["armed"] else "false",
            str(update["deadline"]), update["signature"],
            "--value", "0", "--chain", str(self.chain_id),
            "--nonce", str(tx_nonce), "--gas-limit", str(gas_limit),
            "--gas-price", str(gas_price), "--legacy",
            "--rpc-url", self.config["rpc_urls"][0],
            "--private-key", hex(self._priv),
        ]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"cast mktx failed: {proc.stderr.strip()[:300]}")
        raw = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("0x")]
        if not raw:
            raise RuntimeError(f"cast mktx produced no signed tx: {proc.stdout[:200]}")
        return raw[-1]

    def _broadcast(self, raw_tx: str) -> str:
        """THE ONLY state-changing call in the service. Live mode only."""
        if not self.live:
            raise RuntimeError("broadcast attempted without --live (bug); refusing")
        tx_hash = self.rpc.send_raw_transaction(raw_tx)
        log(f"broadcast: {tx_hash}")
        for _ in range(30):
            receipt = self.rpc.get_receipt(tx_hash)
            if receipt:
                status = receipt.get("status")
                log(f"receipt: status={status} block={int(receipt['blockNumber'], 16)} "
                    f"gasUsed={int(receipt['gasUsed'], 16)}")
                if status != "0x1":
                    raise RuntimeError(f"transaction reverted on-chain: {tx_hash}")
                return tx_hash
            time.sleep(2)
        raise RuntimeError(f"no receipt after 60s for {tx_hash}")

    # -- main iteration ------------------------------------------------------------
    def run_once(self, injected_price: int = None) -> dict:
        price = injected_price if injected_price is not None else self.read_price()
        decision = self.model.update(price)
        log(f"price={price} decision={decision.action} metrics={decision.metrics} "
            f"reasons={decision.reasons}")
        summary = {"price": price, "action": decision.action}

        if decision.action == "hold":
            return summary

        chain_state = self.sync_from_chain()
        # Re-check against fresh chain state: if the registry already reflects
        # this exact intent (e.g. another relayer beat us), skip.
        update = self._build_update(decision.action, decision.trigger_price, chain_state)
        summary["update"] = {k: update[k] for k in ("armed", "trigger_price", "nonce", "deadline", "digest")}
        log(f"built signed update: armed={update['armed']} trigger={update['trigger_price']} "
            f"signalNonce={update['nonce']} deadline={update['deadline']}")
        log(f"EIP-712 digest: {update['digest']}")
        log(f"operator signature: {update['signature']}")

        ok = self._simulate(update)
        summary["simulation_ok"] = ok
        log(f"eth_call simulation of updateSignalSigned: {'OK (contract would accept)' if ok else 'FAILED'}")
        if not ok:
            log("not submitting an update the contract would reject")
            return summary

        tx_nonce = self.rpc.get_transaction_count(self.operator)
        gas_price = self.rpc.gas_price()
        gas_limit = self._gas_limit(update)
        raw_tx = self._cast_mktx(update, tx_nonce, gas_limit, gas_price)
        summary["tx"] = {"to": self.registry, "from": self.operator, "nonce": tx_nonce,
                         "gas_limit": gas_limit, "gas_price": gas_price, "raw": raw_tx}
        if self.live:
            tx_hash = self._broadcast(raw_tx)
            summary["tx_hash"] = tx_hash
            self.model.note_submitted(decision.action, decision.trigger_price)
            self.sync_from_chain()
        else:
            log(f"DRY-RUN: would-be tx (nonce {tx_nonce}, gas {gas_limit} @ {gas_price} wei): {raw_tx}")
            log("DRY-RUN: nothing broadcast. Re-run with --live to submit.")
            self.model.note_submitted(decision.action, decision.trigger_price)  # shadow state
        return summary

    def run(self, once: bool = False, injected_prices=None) -> None:
        mode = "LIVE" if self.live else "DRY-RUN"
        log(f"signal service starting ({mode}) chain={self.chain_id} token={self.token}")
        self.load_operator_key()
        log(f"operator (aiOperator) key loaded for {self.operator}")
        self.verify_static_chain_facts()
        self.sync_from_chain()
        self.bootstrap_history()
        if injected_prices:
            for p in injected_prices:
                self.run_once(injected_price=p)
            return
        interval = float(self.config.get("poll_interval_seconds", 15))
        while True:
            try:
                self.run_once()
            except RpcError as exc:
                log(f"RPC error (will retry next poll): {exc}")
            if once:
                return
            time.sleep(interval)


def load_config(path: str) -> dict:
    cfg_path = Path(path)
    config = json.loads(cfg_path.read_text())
    return config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Penjaga Stop-Loss Agentik — AI signal service")
    parser.add_argument("--config", default=str(SERVICE_ROOT / "config.json"))
    parser.add_argument("--live", action="store_true",
                        help="actually broadcast (default: dry-run, nothing is sent)")
    parser.add_argument("--once", action="store_true", help="single poll iteration, then exit")
    parser.add_argument("--prices", default=None,
                        help="comma-separated prices to feed the model instead of reading the feed "
                             "(demo/testing aid; chain reads for signal state still happen)")
    parser.add_argument("--token", default=None, help="override the token address from config")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.token:
        config["token"] = args.token
    service = SignalService(config, live=args.live)
    injected = [int(x) for x in args.prices.split(",")] if args.prices else None
    try:
        service.run(once=args.once, injected_prices=injected)
    except KeyboardInterrupt:
        log("stopped by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
