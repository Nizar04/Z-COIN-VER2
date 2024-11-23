import asyncio
import hashlib
import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import websockets
import threading
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from aiohttp import web
import aiohttp
from eth_utils import keccak
import struct
import numpy as np
from concurrent.futures import ProcessPoolExecutor
import pyopencl as cl
import queue
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProofOfWork:
    def __init__(self, use_gpu=True):
        self.use_gpu = use_gpu
        if use_gpu:
            try:
                self.setup_gpu()
            except Exception as e:
                logger.warning(f"GPU setup failed: {e}. Falling back to CPU mining.")
                self.use_gpu = False
                
    def setup_gpu(self):
        # Initialize OpenCL context and command queue
        platform = cl.get_platforms()[0]
        device = platform.get_devices()[0]
        self.ctx = cl.Context([device])
        self.queue = cl.CommandQueue(self.ctx)
        
        # OpenCL kernel for SHA-256 mining
        self.kernel_code = """
        __kernel void mine(
            __global const char* block_header,
            __global const uint* target,
            __global uint* result,
            const uint start_nonce,
            const uint iterations
        ) {
            uint nonce = get_global_id(0) + start_nonce;
            uint found = 0;
            
            for (uint i = 0; i < iterations; i++) {
                // SHA-256 implementation
                uint hash[8];
                sha256_init(hash);
                sha256_update(hash, block_header, 80);
                sha256_update(hash, (char*)&nonce, 4);
                sha256_final(hash);
                
                if (hash[0] < target[0]) {
                    result[0] = nonce;
                    result[1] = 1;  // Found flag
                    return;
                }
                nonce++;
            }
        }
        """
        
        self.program = cl.Program(self.ctx, self.kernel_code).build()
        
    async def mine_block(self, block_header: bytes, target: int, start_nonce: int = 0) -> Optional[int]:
        if self.use_gpu:
            return await self.mine_gpu(block_header, target, start_nonce)
        return await self.mine_cpu(block_header, target, start_nonce)
        
    async def mine_gpu(self, block_header: bytes, target: int, start_nonce: int) -> Optional[int]:
        # Prepare OpenCL buffers
        header_buf = cl.Buffer(self.ctx, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=block_header)
        target_buf = cl.Buffer(self.ctx, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=np.array([target], dtype=np.uint32))
        result_buf = cl.Buffer(self.ctx, cl.mem_flags.WRITE_ONLY, size=8)
        
        # Set up work groups
        local_size = 256
        global_size = 16384  # Adjust based on GPU capabilities
        
        while True:
            # Reset result buffer
            cl.enqueue_fill_buffer(self.queue, result_buf, np.array([0, 0], dtype=np.uint32), 0, 8)
            
            # Execute kernel
            self.program.mine(
                self.queue, (global_size,), (local_size,),
                header_buf, target_buf, result_buf,
                np.uint32(start_nonce), np.uint32(1000)
            )
            
            # Check result
            result = np.zeros(2, dtype=np.uint32)
            cl.enqueue_copy(self.queue, result, result_buf)
            
            if result[1]:  # Nonce found
                return int(result[0])
                
            start_nonce += global_size * 1000
            await asyncio.sleep(0)  # Allow other tasks to run
            
    async def mine_cpu(self, block_header: bytes, target: int, start_nonce: int) -> Optional[int]:
        with ProcessPoolExecutor() as executor:
            loop = asyncio.get_event_loop()
            cpu_count = os.cpu_count() or 1
            futures = []
            
            for i in range(cpu_count):
                start = start_nonce + (i * 1000000)
                futures.append(loop.run_in_executor(
                    executor,
                    self._mine_cpu_worker,
                    block_header,
                    target,
                    start,
                    start + 1000000
                ))
                
            for future in asyncio.as_completed(futures):
                result = await future
                if result is not None:
                    return result
                    
        return None
        
    @staticmethod
    def _mine_cpu_worker(block_header: bytes, target: int, start_nonce: int, end_nonce: int) -> Optional[int]:
        for nonce in range(start_nonce, end_nonce):
            header_with_nonce = block_header + struct.pack(">I", nonce)
            hash_result = hashlib.sha256(hashlib.sha256(header_with_nonce).digest()).digest()
            if int.from_bytes(hash_result, 'big') < target:
                return nonce
        return None

class EnhancedBlock(Block):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = 1
        self.merkle_root_hash = self.merkle_root()
        self.verification_signatures = []
        
    def serialize_header(self) -> bytes:
        return struct.pack(
            ">I32s32sIII",
            self.version,
            bytes.fromhex(self.previous_hash),
            self.merkle_root_hash,
            int(self.timestamp),
            self.target,
            self.nonce
        )
        
    def verify(self) -> bool:
        # Verify basic structure
        if not self.hash or not self.previous_hash:
            return False
            
        # Verify proof of work
        header = self.serialize_header()
        pow_hash = hashlib.sha256(hashlib.sha256(header).digest()).digest()
        if int.from_bytes(pow_hash, 'big') >= self.target:
            return False
            
        # Verify transactions
        if not self.verify_transactions():
            return False
            
        # Verify merkle root
        if self.merkle_root_hash != self.merkle_root():
            return False
            
        return True
        
    def verify_transactions(self) -> bool:
        for tx in self.transactions:
            if not self.verify_transaction(tx):
                return False
        return True
        
    @staticmethod
    def verify_transaction(tx: Transaction) -> bool:
        try:
            # Verify signature
            if not tx.verify_signature():
                return False
                
            # Verify basic transaction rules
            if tx.amount <= 0:
                return False
                
            # Additional transaction verification rules can be added here
            
            return True
        except Exception:
            return False

class EnhancedNode(Node):
    def __init__(self, host: str, port: int):
        super().__init__(host, port)
        self.pow = ProofOfWork()
        self.pending_blocks = asyncio.Queue()
        self.verified_blocks = set()
        
    async def start(self):
        # Start block verification worker
        asyncio.create_task(self.block_verification_worker())
        
        # Start network communication
        await super().start()
        
    async def block_verification_worker(self):
        while True:
            block = await self.pending_blocks.get()
            
            # Skip if already verified
            if block.hash in self.verified_blocks:
                continue
                
            # Verify the block
            if await self.verify_block(block):
                self.verified_blocks.add(block.hash)
                await self.broadcast({
                    'type': 'block_verified',
                    'block_hash': block.hash,
                    'signature': self.sign_verification(block.hash)
                })
                
            self.pending_blocks.task_done()
            
    async def verify_block(self, block: EnhancedBlock) -> bool:
        # Basic verification
        if not block.verify():
            return False
            
        # Verify block against current chain state
        if not await self.verify_against_chain(block):
            return False
            
        # Consensus verification (require majority of nodes to verify)
        if not await self.reach_consensus(block):
            return False
            
        return True
        
    async def verify_against_chain(self, block: EnhancedBlock) -> bool:
        # Verify block connects to our chain
        if block.previous_hash != self.blockchain.chain[-1].hash:
            return False
            
        # Verify no double-spending
        if not await self.verify_no_double_spend(block):
            return False
            
        return True
        
    async def verify_no_double_spend(self, block: EnhancedBlock) -> bool:
        spent_outputs = set()
        for tx in block.transactions:
            # Check if inputs are already spent
            for input_ref in tx.inputs:
                if input_ref in spent_outputs:
                    return False
                spent_outputs.add(input_ref)
        return True
        
    async def reach_consensus(self, block: EnhancedBlock) -> bool:
        # Broadcast verification request
        await self.broadcast({
            'type': 'verify_block_request',
            'block': block.to_dict()
        })
        
        # Wait for verification responses
        verifications = []
        try:
            async with asyncio.timeout(10):  # 10 second timeout
                while len(verifications) < len(self.peers) // 2 + 1:
                    response = await self.verification_queue.get()
                    if self.verify_node_signature(response):
                        verifications.append(response)
        except asyncio.TimeoutError:
            return False
            
        return len(verifications) >= len(self.peers) // 2 + 1

class EnhancedBlockchain(Blockchain):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pow = ProofOfWork()
        
    async def mine_block(self, block: EnhancedBlock, websocket=None) -> bool:
        # Prepare block header
        header = block.serialize_header()
        
        # Start mining
        nonce = await self.pow.mine_block(header, block.target)
        if nonce is None:
            return False
            
        # Update block with found nonce
        block.nonce = nonce
        block.hash = block.calculate_hash()
        
        # Notify progress if websocket is available
        if websocket:
            await websocket.send_json({
                'type': 'block_mined',
                'block': block.to_dict()
            })
            
        return True

if __name__ == '__main__':
    # Initialize enhanced blockchain server
    server = BlockchainServer(blockchain_class=EnhancedBlockchain)
    
    # Run the server
    web.run_app(server.app, host='0.0.0.0', port=8080)
