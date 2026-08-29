#!/usr/bin/env python3
"""
NPlus Coin Miner - Standalone Edition
======================================
A proof-of-work blockchain simulation with wallet persistence,
halving mechanism, and dynamic difficulty adjustment.

Features:
- Pure Python (no external dependencies)
- Wallet persistence with atomic writes
- Dynamic difficulty adjustment
- Halving every 100 blocks
- Total supply cap (1 billion NPC)
- Graceful shutdown (Ctrl+C)
- Professional logging

Usage:
    python nplus_miner.py

Author: NPlus Team
License: MIT
"""

import os
import sys
import time
import json
import shutil
import hashlib
import logging
import signal
from pathlib import Path
from datetime import datetime


class Config:
    DATA_DIR = Path("./nplus_data")
    INITIAL_DIFFICULTY = 4
    MIN_DIFFICULTY = 2
    MAX_DIFFICULTY = 8
    DIFFICULTY_ADJUSTMENT_INTERVAL = 20
    TARGET_BLOCK_TIME = 1.5
    INITIAL_BLOCK_REWARD = 50
    HALVING_INTERVAL = 100
    TOTAL_SUPPLY_CAP = 1_000_000_000
    STATUS_REPORT_INTERVAL = 30
    CHECKPOINT_LOG_INTERVAL = 20
    MAX_RECENT_BLOCKS = 100
    FORCE_RESET = False


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "miner.log"
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("NPlusMiner")


def atomic_write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    tmp_path.replace(path)


def read_json(path: Path, default=None):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def generate_address() -> str:
    seed = f"NPlusMiner_{time.time()}_{os.getpid()}"
    return "0x" + hashlib.sha256(seed.encode()).hexdigest()[:40]


class Block:
    def __init__(self, index: int, previous_hash: str, timestamp: float,
                 data: dict, nonce: int = 0, block_hash: str = None):
        self.index = index
        self.previous_hash = previous_hash
        self.timestamp = timestamp
        self.data = data
        self.nonce = nonce
        self.hash = block_hash or self.compute_hash()

    def compute_hash(self) -> str:
        payload = {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "data": self.data,
            "nonce": self.nonce,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "data": self.data,
            "nonce": self.nonce,
            "hash": self.hash,
        }

    @staticmethod
    def from_dict(d: dict) -> "Block":
        return Block(
            index=d["index"],
            previous_hash=d["previous_hash"],
            timestamp=d["timestamp"],
            data=d["data"],
            nonce=d["nonce"],
            block_hash=d["hash"],
        )


class WalletManager:
    def __init__(self, wallet_path: Path, logger: logging.Logger, default_address: str):
        self.wallet_path = wallet_path
        self.logger = logger
        self.default_address = default_address
        self.data = self._load_or_create()

    def _load_or_create(self) -> dict:
        existing = read_json(self.wallet_path)
        if existing:
            existing.setdefault("address", self.default_address)
            existing.setdefault("balance", 0)
            existing.setdefault("blocks_mined", 0)
            existing.setdefault("total_rewards_claimed", existing.get("balance", 0))
            existing.setdefault("created_at", datetime.now().isoformat())
            existing.setdefault("last_updated", None)
            self.logger.info(
                f"Wallet loaded: {existing['balance']:,} NPC | "
                f"{existing['blocks_mined']} blocks | {existing['address'][:16]}..."
            )
            return existing

        data = {
            "address": self.default_address or generate_address(),
            "balance": 0,
            "blocks_mined": 0,
            "total_rewards_claimed": 0,
            "created_at": datetime.now().isoformat(),
            "last_updated": None,
        }
        atomic_write_json(self.wallet_path, data)
        self.logger.info(f"New wallet created: {data['address'][:16]}...")
        return data

    def save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        atomic_write_json(self.wallet_path, self.data)

    def add_reward(self, amount: int) -> int:
        self.data["balance"] += amount
        self.data["blocks_mined"] += 1
        self.data["total_rewards_claimed"] += amount
        self.save()
        return self.data["balance"]

    def get_balance(self) -> int:
        return self.data["balance"]

    def get_total_rewards(self) -> int:
        return self.data["total_rewards_claimed"]

    def get_summary(self) -> str:
        return (
            f"{self.data['balance']:,} NPC | "
            f"{self.data['blocks_mined']} blocks | "
            f"{self.data['address'][:16]}..."
        )


class ChainStorage:
    def __init__(self, data_dir: Path, logger: logging.Logger):
        self.state_file = data_dir / "chain_state.json"
        self.logger = logger

    def load(self) -> dict:
        state = read_json(self.state_file)
        if state:
            self.logger.info(
                f"Chain state loaded: height={state.get('height', 0)} | "
                f"difficulty={state.get('difficulty', '?')}"
            )
        return state

    def save(self, blockchain: "NPlusBlockchain"):
        data = {
            "height": blockchain.height,
            "difficulty": blockchain.difficulty,
            "total_mined_coins": blockchain.total_mined_coins,
            "last_hash": blockchain.latest_block.hash,
            "last_saved": datetime.now().isoformat(),
            "recent_blocks": [b.to_dict() for b in blockchain.recent_blocks[-Config.MAX_RECENT_BLOCKS:]],
            "recent_block_times": blockchain.block_times[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:],
        }
        atomic_write_json(self.state_file, data)


class NPlusBlockchain:
    DEFAULT_CONFIG = {
        "network": "NPlus Mainnet",
        "chain_id": 8888,
        "symbol": "NPC",
        "coin_name": "NPlus Coin",
        "difficulty": Config.INITIAL_DIFFICULTY,
        "initial_block_reward": Config.INITIAL_BLOCK_REWARD,
        "total_supply_cap": Config.TOTAL_SUPPLY_CAP,
        "miner_address": None,
    }

    def __init__(self, config_path: Path, logger: logging.Logger, saved_state: dict = None):
        self.config = self._load_config(config_path)
        self.logger = logger
        self.symbol = self.config.get("symbol", "NPC")
        self.coin_name = self.config.get("coin_name", "NPlus Coin")
        self.initial_reward = int(self.config.get("initial_block_reward", Config.INITIAL_BLOCK_REWARD))
        self.total_supply_cap = int(self.config.get("total_supply_cap", Config.TOTAL_SUPPLY_CAP))
        self.difficulty = int(self.config.get("difficulty", Config.INITIAL_DIFFICULTY))
        self.height = 0
        self.total_mined_coins = 0
        self.block_times = []
        self.recent_blocks = []
        self.latest_block = None
        if saved_state and saved_state.get("recent_blocks"):
            self._restore_from_state(saved_state)
        else:
            self._create_genesis_block()

    def _load_config(self, path: Path) -> dict:
        config = read_json(path, dict(self.DEFAULT_CONFIG))
        changed = False
        for k, v in self.DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
                changed = True
        if changed or not path.exists():
            atomic_write_json(path, config)
            self.logger.info("Network config saved.")
        return config

    def _create_genesis_block(self):
        genesis = Block(
            index=0,
            previous_hash="0" * 64,
            timestamp=time.time(),
            data={
                "type": "genesis",
                "message": f"{self.coin_name} Genesis Block",
                "timestamp": datetime.now().isoformat(),
            },
        )
        self.latest_block = genesis
        self.height = 0
        self.recent_blocks = [genesis]
        self.logger.info(f"Genesis block created: {genesis.hash[:16]}...")

    def _restore_from_state(self, state: dict):
        try:
            blocks = [Block.from_dict(b) for b in state["recent_blocks"]]
            self.recent_blocks = blocks[-Config.MAX_RECENT_BLOCKS:]
            self.latest_block = self.recent_blocks[-1]
            self.height = int(state.get("height", self.latest_block.index))
            self.difficulty = int(state.get("difficulty", self.difficulty))
            self.total_mined_coins = int(state.get("total_mined_coins", 0))
            self.block_times = state.get("recent_block_times", [])[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:]
            self.logger.info(
                f"Blockchain restored: height={self.height} | "
                f"latest={self.latest_block.hash[:16]}..."
            )
        except Exception as e:
            self.logger.error(f"Failed to restore chain state: {e}")
            self._create_genesis_block()

    def get_latest_block(self) -> Block:
        return self.latest_block

    def get_reward_for_next_block(self) -> int:
        next_height = self.height + 1
        halvings = (next_height - 1) // Config.HALVING_INTERVAL
        reward = self.initial_reward // (2 ** halvings)
        reward = max(reward, 1)
        remaining = self.total_supply_cap - self.total_mined_coins
        return max(0, min(reward, remaining))

    def proof_of_work(self, block: Block, difficulty: int) -> tuple:
        target_prefix = "0" * difficulty
        block.nonce = 0
        computed_hash = block.compute_hash()
        hashes = 1
        start = time.time()
        while not computed_hash.startswith(target_prefix):
            block.nonce += 1
            computed_hash = block.compute_hash()
            hashes += 1
        duration = time.time() - start
        block.hash = computed_hash
        self.block_times.append(duration)
        return hashes, duration

    def add_block(self, block: Block, difficulty_used: int) -> bool:
        latest = self.get_latest_block()
        if block.previous_hash != latest.hash:
            self.logger.error("Block rejected: previous hash mismatch.")
            return False
        if not block.hash.startswith("0" * difficulty_used):
            self.logger.error("Block rejected: invalid proof of work.")
            return False
        self.latest_block = block
        self.height = block.index
        self.recent_blocks.append(block)
        self.recent_blocks = self.recent_blocks[-Config.MAX_RECENT_BLOCKS:]
        self.adjust_difficulty()
        return True

    def adjust_difficulty(self):
        if len(self.block_times) < Config.DIFFICULTY_ADJUSTMENT_INTERVAL:
            return
        recent = self.block_times[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:]
        avg_time = sum(recent) / len(recent)
        old = self.difficulty
        if avg_time < Config.TARGET_BLOCK_TIME * 0.5:
            self.difficulty = min(Config.MAX_DIFFICULTY, self.difficulty + 1)
        elif avg_time > Config.TARGET_BLOCK_TIME * 2.0:
            self.difficulty = max(Config.MIN_DIFFICULTY, self.difficulty - 1)
        self.block_times.clear()
        if self.difficulty != old:
            self.logger.info(
                f"Difficulty adjusted: {old} -> {self.difficulty} "
                f"(avg block time: {avg_time:.2f}s)"
            )

    def supply_reached(self) -> bool:
        return self.total_mined_coins >= self.total_supply_cap


def setup_project_structure(logger: logging.Logger):
    base = Config.DATA_DIR
    if Config.FORCE_RESET and base.exists():
        shutil.rmtree(base)
        logger.warning("FORCE_RESET enabled: all data wiped.")
    for d in ["config", "wallet", "data", "logs", "contracts"]:
        (base / d).mkdir(parents=True, exist_ok=True)

    contract_path = base / "contracts" / "NPlusCoin.sol"
    if not contract_path.exists():
        sol = '''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract NPlusCoin {
    string public name = "NPlus Coin";
    string public symbol = "NPC";
    uint256 public totalSupply = 1000000000 * 10**18;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    constructor() {
        balanceOf[msg.sender] = totalSupply;
        emit Transfer(address(0), msg.sender, totalSupply);
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        require(to != address(0), "Invalid recipient");
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) public returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public returns (bool) {
        require(to != address(0), "Invalid recipient");
        require(balanceOf[from] >= amount, "Insufficient balance");
        require(allowance[from][msg.sender] >= amount, "Allowance exceeded");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        allowance[from][msg.sender] -= amount;
        emit Transfer(from, to, amount);
        return true;
    }
}
'''
        contract_path.write_text(sol, encoding="utf-8")
        logger.info("Smart contract template created.")

    readme_path = base / "README.md"
    if not readme_path.exists():
        readme = '''# NPlus Coin Miner

A proof-of-work blockchain simulation with wallet persistence, halving mechanism, and dynamic difficulty adjustment.

## Run

```bash
python nplus_miner.py
```

## Data layout

```
nplus_data/
├── config/network.json
├── wallet/balance.json
├── data/chain_state.json
├── logs/miner.log
└── contracts/NPlusCoin.sol
```

NPC is a simulated token for learning purposes only.
'''
        readme_path.write_text(readme, encoding="utf-8")
    logger.info(f"Project ready at: {base.absolute()}")


class NPlusMiner:
    def __init__(self):
        self.running = False
        self.logger = None
        self.blockchain = None
        self.wallet = None
        self.storage = None
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        if self.logger:
            self.logger.info("\nShutdown signal received. Saving state...")
        self.running = False

    def initialize(self):
        log_dir = Config.DATA_DIR / "logs"
        self.logger = setup_logging(log_dir)
        self.logger.info("=" * 60)
        self.logger.info("NPLUS COIN MINER - STANDALONE EDITION")
        self.logger.info("=" * 60)
        setup_project_structure(self.logger)
        config_path = Config.DATA_DIR / "config" / "network.json"
        data_dir = Config.DATA_DIR / "data"
        self.storage = ChainStorage(data_dir, self.logger)
        saved_state = self.storage.load()
        self.blockchain = NPlusBlockchain(config_path, self.logger, saved_state)
        wallet_path = Config.DATA_DIR / "wallet" / "balance.json"
        default_address = self.blockchain.config.get("miner_address") or generate_address()
        if not self.blockchain.config.get("miner_address"):
            self.blockchain.config["miner_address"] = default_address
            atomic_write_json(config_path, self.blockchain.config)
        self.wallet = WalletManager(wallet_path, self.logger, default_address)
        wallet_total = self.wallet.get_total_rewards()
        if wallet_total > self.blockchain.total_mined_coins:
            self.blockchain.total_mined_coins = wallet_total
            self.logger.warning(
                f"Wallet rewards ({wallet_total:,}) > chain state. Using wallet total as baseline."
            )
        self.logger.info(f"Wallet: {self.wallet.get_summary()}")
        self.logger.info(f"Supply cap: {self.blockchain.total_supply_cap:,} {self.blockchain.symbol}")
        self.logger.info("=" * 60)

    def run(self):
        self.running = True
        session_blocks = 0
        session_rewards = 0
        total_hashes = 0
        start_time = time.time()
        last_report = start_time
        self.logger.info(f"Mining started | Difficulty: {self.blockchain.difficulty}")
        self.logger.info(f"Target block time: {Config.TARGET_BLOCK_TIME}s")
        self.logger.info("-" * 60)
        try:
            while self.running:
                if self.blockchain.supply_reached():
                    self.logger.info("Total supply reached. Mining complete.")
                    break
                reward = self.blockchain.get_reward_for_next_block()
                if reward <= 0:
                    self.logger.info("No remaining reward. Mining complete.")
                    break
                latest = self.blockchain.get_latest_block()
                next_index = self.blockchain.height + 1
                difficulty_used = self.blockchain.difficulty
                block_data = {
                    "type": "mining_reward",
                    "height": next_index,
                    "miner": self.wallet.data["address"],
                    "reward": reward,
                    "symbol": self.blockchain.symbol,
                    "timestamp": datetime.now().isoformat(),
                }
                new_block = Block(
                    index=next_index,
                    previous_hash=latest.hash,
                    timestamp=time.time(),
                    data=block_data,
                )
                hashes, duration = self.blockchain.proof_of_work(new_block, difficulty_used)
                total_hashes += hashes
                if self.blockchain.add_block(new_block, difficulty_used):
                    self.blockchain.total_mined_coins += reward
                    new_balance = self.wallet.add_reward(reward)
                    self.storage.save(self.blockchain)
                    session_blocks += 1
                    session_rewards += reward
                    block_rate = hashes / duration if duration > 0 else 0
                    self.logger.info(
                        f"Block #{new_block.index} | diff={difficulty_used} | "
                        f"nonce={new_block.nonce:,} | +{reward} {self.blockchain.symbol} | "
                        f"balance={new_balance:,} | {block_rate:,.0f} H/s | {new_block.hash[:16]}..."
                    )
                    if session_blocks % Config.CHECKPOINT_LOG_INTERVAL == 0:
                        self.logger.info(
                            f"Checkpoint | height={self.blockchain.height} | "
                            f"balance={self.wallet.get_balance():,} {self.blockchain.symbol}"
                        )
                now = time.time()
                if now - last_report >= Config.STATUS_REPORT_INTERVAL:
                    elapsed = now - start_time
                    avg_hr = total_hashes / elapsed if elapsed > 0 else 0
                    self.logger.info(
                        f"Status | blocks={session_blocks} | rewards={session_rewards:,} {self.blockchain.symbol} | "
                        f"hashes={total_hashes:,} | avg={avg_hr:,.0f} H/s | balance={self.wallet.get_balance():,} | "
                        f"height={self.blockchain.height} | diff={self.blockchain.difficulty}"
                    )
                    last_report = now
        except Exception as e:
            self.logger.error(f"Mining error: {e}", exc_info=True)
        finally:
            self.wallet.save()
            self.storage.save(self.blockchain)
            elapsed = time.time() - start_time
            avg_hr = total_hashes / elapsed if elapsed > 0 else 0
            self.logger.info("=" * 60)
            self.logger.info("SESSION SUMMARY")
            self.logger.info(f"Session blocks: {session_blocks}")
            self.logger.info(f"Session rewards: {session_rewards:,} {self.blockchain.symbol}")
            self.logger.info(f"Final balance: {self.wallet.get_summary()}")
            self.logger.info(f"Chain height: {self.blockchain.height}")
            self.logger.info(f"Difficulty: {self.blockchain.difficulty}")
            self.logger.info(f"Total hashes: {total_hashes:,}")
            self.logger.info(f"Average hashrate: {avg_hr:,.2f} H/s")
            self.logger.info(f"Wallet file: {Config.DATA_DIR / 'wallet' / 'balance.json'}")
            self.logger.info(f"Chain state: {Config.DATA_DIR / 'data' / 'chain_state.json'}")
            self.logger.info("=" * 60)


def main():
    miner = NPlusMiner()
    miner.initialize()
    miner.run()


if __name__ == "__main__":
    main()
