"""JSON-RPC client over curl.

curl is used deliberately: it is the transport proven to work from this VM
(it honours the egress proxy; the Rust HTTP client inside `cast send` gets
its TLS connections dropped). Read calls fail over across the configured RPC
URLs. `send_raw_transaction` is the ONLY method that can change chain state,
and the service calls it exclusively from its --live path.
"""

import json
import re
import subprocess


class RpcError(Exception):
    pass


class RpcClient:
    def __init__(self, urls, timeout=30):
        if not urls:
            raise ValueError("at least one RPC URL is required")
        self.urls = list(urls)
        self.timeout = timeout
        self._id = 0

    def _post(self, url: str, method: str, params: list):
        self._id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(self.timeout), "-X", "POST", url,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RpcError(f"transport failure calling {method} on {url}: rc={proc.returncode}")
        try:
            body = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RpcError(f"non-JSON response calling {method} on {url}")
        if "error" in body and body["error"]:
            err = body["error"]
            raise RpcError(f"RPC error calling {method}: {err.get('message', err)}")
        return body.get("result")

    def call(self, method: str, params: list):
        """Read-only call with failover across the configured URLs."""
        last = None
        for url in self.urls:
            try:
                return self._post(url, method, params)
            except RpcError as exc:
                last = exc
        raise last

    # -- reads -------------------------------------------------------------
    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_timestamp(self, block: str = "latest") -> int:
        result = self.call("eth_getBlockByNumber", [block, False])
        return int(result["timestamp"], 16)

    def eth_call(self, to: str, data_hex: str, from_addr: str = None) -> bytes:
        tx = {"to": to, "data": data_hex}
        if from_addr:
            tx["from"] = from_addr
        result = self.call("eth_call", [tx, "latest"])
        return bytes.fromhex(result[2:] if result.startswith("0x") else result)

    def get_transaction_count(self, address: str) -> int:
        return int(self.call("eth_getTransactionCount", [address, "pending"]), 16)

    def gas_price(self) -> int:
        return int(self.call("eth_gasPrice", []), 16)

    def estimate_gas(self, from_addr: str, to: str, data_hex: str) -> int:
        result = self.call("eth_estimateGas", [{"from": from_addr, "to": to, "data": data_hex}])
        return int(result, 16)

    def get_logs(self, address: str, topic0: str, topic1: str, from_block: int, to_block: int) -> list:
        return self.call("eth_getLogs", [{
            "address": address,
            "topics": [topic0, topic1],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }])

    def get_logs_adaptive(self, address: str, topic0: str, topic1: str,
                          from_block: int, to_block: int, initial_chunk: int = 10000) -> list:
        """Fetch logs over a wide range, shrinking the chunk when an RPC
        enforces a range limit (e.g. sepolia.base.org caps at 200 blocks)."""
        logs = []
        chunk = initial_chunk
        cursor = from_block
        while cursor <= to_block:
            end = min(cursor + chunk - 1, to_block)
            try:
                logs.extend(self.get_logs(address, topic0, topic1, cursor, end))
                cursor = end + 1
            except RpcError as exc:
                match = re.search(r"limited to a (\d+) range", str(exc))
                if match:
                    limit = int(match.group(1))
                    if limit >= 1 and limit < chunk:
                        chunk = limit
                        continue
                if chunk > 200:
                    chunk //= 2
                    continue
                raise
        return logs

    # -- the one state-changing method (live mode only) --------------------
    def send_raw_transaction(self, raw_tx_hex: str) -> str:
        """Broadcast a signed transaction. Returns the transaction hash."""
        return self._post(self.urls[0], "eth_sendRawTransaction", [raw_tx_hex])

    def get_receipt(self, tx_hash: str):
        return self.call("eth_getTransactionReceipt", [tx_hash])
