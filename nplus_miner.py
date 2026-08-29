#!/usr/bin/env python3
"""
NPlus Coin Miner - Standalone Edition - HARDWARE MINING
Minh bạch 100%: Coin = Hash thật / phần cứng
"""

import os
import sys
import time
import json
import shutil
import hashlib
import logging
import signal
import platform
import multiprocessing
from pathlib import Path
from datetime import datetime

class Config:
    DATA_DIR = Path("./nplus_data")
    INITIAL_DIFFICULTY = 4
    MIN_DIFFICULTY = 3
    MAX_DIFFICULTY = 5
    DIFFICULTY_ADJUSTMENT_INTERVAL = 15
    TARGET_BLOCK_TIME = 1.2
    HASHES_PER_NPC = 1300 # MINH BẠCH: 1300 hash = 1 NPC
    BASE_REWARD = 10 # thưởng tìm ra block
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
    logger = logging.getLogger("NPlusMiner")
    logger.info(f"HARDWARE | CPU: {platform.processor()} | Cores: {os.cpu_count()} | {Config.HASHES_PER_NPC} hash = 1 NPC")
    return logger

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
    def __init__(self, index: int, previous_hash: str, timestamp: float, data: dict, nonce: int = 0, block_hash: str = None):
        self.index = index; self.previous_hash = previous_hash; self.timestamp = timestamp; self.data = data; self.nonce = nonce
        self.hash = block_hash or self.compute_hash()
    def compute_hash(self) -> str:
        payload = {"index": self.index,"previous_hash": self.previous_hash,"timestamp": self.timestamp,"data": self.data,"nonce": self.nonce}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    def to_dict(self) -> dict:
        return {"index": self.index,"previous_hash": self.previous_hash,"timestamp": self.timestamp,"data": self.data,"nonce": self.nonce,"hash": self.hash}
    @staticmethod
    def from_dict(d: dict) -> "Block":
        return Block(d["index"], d["previous_hash"], d["timestamp"], d["data"], d["nonce"], d["hash"])

class WalletManager:
    def __init__(self, wallet_path: Path, logger: logging.Logger, default_address: str):
        self.wallet_path = wallet_path; self.logger = logger; self.default_address = default_address; self.data = self._load_or_create()
    def _load_or_create(self) -> dict:
        existing = read_json(self.wallet_path)
        if existing:
            self.logger.info(f"Wallet loaded: {existing['balance']:,} NPC | {existing['blocks_mined']} blocks | {existing['address'][:16]}...")
            return existing
        data = {"address": self.default_address or generate_address(),"balance": 0,"blocks_mined": 0,"total_rewards_claimed": 0,"created_at": datetime.now().isoformat(),"last_updated": None}
        atomic_write_json(self.wallet_path, data); self.logger.info(f"New wallet created: {data['address'][:16]}..."); return data
    def save(self):
        self.data["last_updated"] = datetime.now().isoformat(); atomic_write_json(self.wallet_path, self.data)
    def add_reward(self, amount: int) -> int:
        self.data["balance"] += amount; self.data["blocks_mined"] += 1; self.data["total_rewards_claimed"] += amount; self.save(); return self.data["balance"]
    def get_balance(self) -> int: return self.data["balance"]
    def get_total_rewards(self) -> int: return self.data["total_rewards_claimed"]
    def get_summary(self) -> str: return f"{self.data['balance']:,} NPC | {self.data['blocks_mined']} blocks | {self.data['address'][:16]}..."

class ChainStorage:
    def __init__(self, data_dir: Path, logger: logging.Logger): self.state_file = data_dir / "chain_state.json"; self.logger = logger
    def load(self) -> dict:
        state = read_json(self.state_file)
        if state: self.logger.info(f"Chain state loaded: height={state.get('height', 0)} | difficulty={state.get('difficulty', '?')}")
        return state
    def save(self, blockchain: "NPlusBlockchain"):
        data = {"height": blockchain.height,"difficulty": blockchain.difficulty,"total_mined_coins": blockchain.total_mined_coins,"last_hash": blockchain.latest_block.hash,"last_saved": datetime.now().isoformat(),"recent_blocks": [b.to_dict() for b in blockchain.recent_blocks[-Config.MAX_RECENT_BLOCKS:]],"recent_block_times": blockchain.block_times[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:]}
        atomic_write_json(self.state_file, data)

# WORKER CHẠY TRÊN TỪNG CORE - ĐÂY MỚI LÀ CÀY BẰNG PHẦN CỨNG THẬT
def _pow_worker(args):
    index, previous_hash, timestamp, data, start_nonce, step, difficulty = args
    target_prefix = "0" * difficulty; nonce = start_nonce; hashes = 0
    while True:
        payload = {"index": index,"previous_hash": previous_hash,"timestamp": timestamp,"data": data,"nonce": nonce}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        h = hashlib.sha256(encoded).hexdigest(); hashes += 1
        if h.startswith(target_prefix): return (nonce, h, hashes)
        nonce += step

class NPlusBlockchain:
    DEFAULT_CONFIG = {"network": "NPlus Mainnet","chain_id": 8888,"symbol": "NPC","coin_name": "NPlus Coin","difficulty": Config.INITIAL_DIFFICULTY,"total_supply_cap": Config.TOTAL_SUPPLY_CAP,"miner_address": None}
    def __init__(self, config_path: Path, logger: logging.Logger, saved_state: dict = None):
        self.config = self._load_config(config_path); self.logger = logger; self.symbol = self.config.get("symbol", "NPC")
        self.coin_name = self.config.get("coin_name", "NPlus Coin"); self.total_supply_cap = int(self.config.get("total_supply_cap", Config.TOTAL_SUPPLY_CAP))
        self.difficulty = min(4, int(self.config.get("difficulty", Config.INITIAL_DIFFICULTY))); self.height = 0; self.total_mined_coins = 0; self.block_times = []; self.recent_blocks = []; self.latest_block = None
        self.cores = os.cpu_count() or 1
        if saved_state and saved_state.get("recent_blocks"): self._restore_from_state(saved_state)
        else: self._create_genesis_block()
        if self.difficulty > 4: self.difficulty = 4
    def _load_config(self, path: Path) -> dict:
        config = read_json(path, dict(self.DEFAULT_CONFIG)); changed = False
        for k, v in self.DEFAULT_CONFIG.items():
            if k not in config: config[k] = v; changed = True
        if changed or not path.exists(): atomic_write_json(path, config)
        return config
    def _create_genesis_block(self):
        genesis = Block(0, "0" * 64, time.time(), {"type": "genesis", "message": f"{self.coin_name} Genesis Block"})
        self.latest_block = genesis; self.height = 0; self.recent_blocks = [genesis]
        self.logger.info(f"Genesis block created: {genesis.hash[:16]}... | Cores: {self.cores}")
    def _restore_from_state(self, state: dict):
        try:
            blocks = [Block.from_dict(b) for b in state["recent_blocks"]]; self.recent_blocks = blocks[-Config.MAX_RECENT_BLOCKS:]; self.latest_block = self.recent_blocks[-1]
            self.height = int(state.get("height", self.latest_block.index)); self.difficulty = min(4, int(state.get("difficulty", self.difficulty)))
            self.total_mined_coins = int(state.get("total_mined_coins", 0)); self.block_times = state.get("recent_block_times", [])[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:]
            self.logger.info(f"Blockchain restored: height={self.height} | cores={self.cores}")
        except Exception as e: self.logger.error(f"Failed to restore chain state: {e}"); self._create_genesis_block()
    def get_latest_block(self) -> Block: return self.latest_block
    def get_reward_from_hashes(self, hashes: int) -> int:
        # MINH BẠCH: COIN TÍNH TỪ HASH THẬT
        coins_from_work = hashes // Config.HASHES_PER_NPC
        reward = Config.BASE_REWARD + coins_from_work
        remaining = self.total_supply_cap - self.total_mined_coins
        return max(0, min(reward, remaining))
    def proof_of_work(self, block: Block, difficulty: int) -> tuple:
        start_time = time.time(); num_workers = self.cores
        worker_args = [(block.index, block.previous_hash, block.timestamp, block.data, i, num_workers, difficulty) for i in range(num_workers)]
        try:
            with multiprocessing.Pool(processes=num_workers) as pool:
                for result in pool.imap_unordered(_pow_worker, worker_args):
                    nonce, hash_result, hashes_per_worker = result
                    total_hashes = hashes_per_worker * num_workers
                    duration = time.time() - start_time
                    block.nonce = nonce; block.hash = hash_result; self.block_times.append(duration); pool.terminate()
                    return total_hashes, duration
        except Exception:
            # Fallback 1 core nếu lỗi pool
            target_prefix = "0" * difficulty; block.nonce = 0; computed_hash = block.compute_hash(); hashes = 1; start = time.time()
            while not computed_hash.startswith(target_prefix):
                block.nonce += 1; computed_hash = block.compute_hash(); hashes += 1
            duration = time.time() - start; block.hash = computed_hash; self.block_times.append(duration); return hashes, duration
    def add_block(self, block: Block, difficulty_used: int) -> bool:
        latest = self.get_latest_block()
        if block.previous_hash!= latest.hash: return False
        if not block.hash.startswith("0" * difficulty_used): return False
        self.latest_block = block; self.height = block.index; self.recent_blocks.append(block)
        self.recent_blocks = self.recent_blocks[-Config.MAX_RECENT_BLOCKS:]; self.adjust_difficulty(); return True
    def adjust_difficulty(self):
        if len(self.block_times) < Config.DIFFICULTY_ADJUSTMENT_INTERVAL: return
        recent = self.block_times[-Config.DIFFICULTY_ADJUSTMENT_INTERVAL:]; avg_time = sum(recent) / len(recent); old = self.difficulty
        if avg_time < Config.TARGET_BLOCK_TIME * 0.5: self.difficulty = min(Config.MAX_DIFFICULTY, self.difficulty + 1)
        elif avg_time > Config.TARGET_BLOCK_TIME * 2.0: self.difficulty = max(Config.MIN_DIFFICULTY, self.difficulty - 1)
        self.block_times.clear()
        if self.difficulty!= old: self.logger.info(f"Difficulty adjusted: {old} -> {self.difficulty} (avg: {avg_time:.2f}s | cores: {self.cores})")
    def supply_reached(self) -> bool: return self.total_mined_coins >= self.total_supply_cap

def setup_project_structure(logger: logging.Logger):
    base = Config.DATA_DIR
    if Config.FORCE_RESET and base.exists(): shutil.rmtree(base)
    for d in ["config", "wallet", "data", "logs", "contracts"]: (base / d).mkdir(parents=True, exist_ok=True)

class NPlusMiner:
    def __init__(self): self.running = False; self.logger = None; self.blockchain = None; self.wallet = None; self.storage = None; signal.signal(signal.SIGINT, self._signal_handler); signal.signal(signal.SIGTERM, self._signal_handler)
    def _signal_handler(self, signum, frame): self.running = False
    def initialize(self):
        self.logger = setup_logging(Config.DATA_DIR / "logs")
        self.logger.info("=" * 60); self.logger.info("NPLUS COIN MINER - HARDWARE MINING EDITION"); self.logger.info("=" * 60)
        setup_project_structure(self.logger); config_path = Config.DATA_DIR / "config" / "network.json"; data_dir = Config.DATA_DIR / "data"
        self.storage = ChainStorage(data_dir, self.logger); saved_state = self.storage.load()
        self.blockchain = NPlusBlockchain(config_path, self.logger, saved_state)
        wallet_path = Config.DATA_DIR / "wallet" / "balance.json"; default_address = self.blockchain.config.get("miner_address") or generate_address()
        if not self.blockchain.config.get("miner_address"): self.blockchain.config["miner_address"] = default_address; atomic_write_json(config_path, self.blockchain.config)
        self.wallet = WalletManager(wallet_path, self.logger, default_address)
        wallet_total = self.wallet.get_total_rewards()
        if wallet_total > self.blockchain.total_mined_coins: self.blockchain.total_mined_coins = wallet_total
        self.logger.info(f"Wallet: {self.wallet.get_summary()} | {self.blockchain.cores} cores | {Config.HASHES_PER_NPC} hash = 1 NPC")
        self.logger.info("=" * 60)
    def run(self):
        self.running = True; session_blocks = 0; session_rewards = 0; total_hashes = 0; start_time = time.time(); last_report = start_time
        self.logger.info(f"Mining started | Diff: {self.blockchain.difficulty} | Mode: HASH -> COIN | Cores: {self.blockchain.cores}")
        try:
            while self.running:
                if self.blockchain.supply_reached(): break
                latest = self.blockchain.get_latest_block(); next_index = self.blockchain.height + 1; difficulty_used = self.blockchain.difficulty
                block_data = {"type": "mining_reward","height": next_index,"miner": self.wallet.data["address"],"symbol": self.blockchain.symbol,"timestamp": datetime.now().isoformat()}
                new_block = Block(next_index, latest.hash, time.time(), block_data)
                hashes, duration = self.blockchain.proof_of_work(new_block, difficulty_used)
                reward = self.blockchain.get_reward_from_hashes(hashes)
                if reward <= 0: break
                total_hashes += hashes
                if self.blockchain.add_block(new_block, difficulty_used):
                    self.blockchain.total_mined_coins += reward; new_balance = self.wallet.add_reward(reward); self.storage.save(self.blockchain)
                    session_blocks += 1; session_rewards += reward
                    block_rate = hashes / duration if duration > 0 else 0; per_core = block_rate / self.blockchain.cores
                    self.logger.info(f"Block #{new_block.index} | {hashes:,} hash -> {reward} NPC ({hashes//Config.HASHES_PER_NPC}+{Config.BASE_REWARD} base) | {block_rate:,.0f} H/s ({per_core:,.0f}/core) | bal={new_balance:,} | {new_block.hash[:16]}...")
                now = time.time()
                if now - last_report >= Config.STATUS_REPORT_INTERVAL:
                    elapsed = now - start_time; avg_hr = total_hashes / elapsed if elapsed > 0 else 0
                    self.logger.info(f"Status | blocks={session_blocks} | rewards={session_rewards:,} | avg={avg_hr:,.0f} H/s on {self.blockchain.cores} cores | height={self.blockchain.height} | diff={self.blockchain.difficulty}")
                    last_report = now
        finally:
            self.wallet.save(); self.storage.save(self.blockchain); self.logger.info(f"Final: {self.wallet.get_summary()} | Total {total_hashes:,} hash = {total_hashes//Config.HASHES_PER_NPC:,} NPC work")

def main():
    miner = NPlusMiner(); miner.initialize(); miner.run()

if __name__ == "__main__":
    main()
