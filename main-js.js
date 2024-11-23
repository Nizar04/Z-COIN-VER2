// WebSocket Connection
let ws = null;
let mining = false;
let blockHeight = 0;

// Initialize WebSocket connection
function initWebSocket() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    
    ws.onopen = () => {
        updateNetworkStatus(true);
        showNotification('Connected to network', 'success');
    };
    
    ws.onclose = () => {
        updateNetworkStatus(false);
        setTimeout(initWebSocket, 5000); // Reconnect after 5 seconds
    };
    
    ws.onmessage = handleWebSocketMessage;
}

// Message Handler
function handleWebSocketMessage(event) {
    const data = JSON.parse(event.data);
    
    switch(data.type) {
        case 'mining_progress':
            updateMiningProgress(data);
            break;
        case 'block_mined':
            handleNewBlock(data.block);
            break;
        case 'transaction_confirmed':
            handleTransactionConfirmed(data.transaction);
            break;
        case 'network_status':
            updateNetworkStatus(data.connected);
            break;
    }
}

// Mining Controls
document.getElementById('startMining').addEventListener('click', async () => {
    if (!mining) {
        mining = true;
        document.getElementById('startMining').classList.add('hidden');
        document.getElementById('stopMining').classList.remove('hidden');
        document.getElementById('miningProgress').classList.remove('hidden');
        
        ws.send(JSON.stringify({
            type: 'start_mining'
        }));
    }
});

document.getElementById('stopMining').addEventListener('click', () => {
    if (mining) {
        mining = false;
        document.getElementById('stopMining').classList.add('hidden');
        document.getElementById('startMining').classList.remove('hidden');
        document.getElementById('miningProgress').classList.add('hidden');
        
        ws.send(JSON.stringify({
            type: 'stop_mining'
        }));
    }
});

// Transaction Form
document.getElementById('transactionForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const transaction = {
        recipient: document.getElementById('recipientAddress').value,
        amount: parseFloat(document.getElementById('amount').value)
    };
    
    try {
        const response = await fetch('/transaction', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(transaction)
        });
        
        if (response.ok) {
            showNotification('Transaction submitted successfully', 'success');
            document.getElementById('transactionForm').reset();
        } else {
            showNotification('Transaction failed', 'error');
        }
    } catch (error) {
        showNotification('Network error', 'error');
    }
});

// Smart Contract Deployment
document.getElementById('deployContract').addEventListener('click', async () => {
    const contractCode = document.getElementById('contractCode').value;
    
    try {
        const response = await fetch('/contract', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                code: contractCode
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            showNotification(`Contract deployed at ${data.contract_address}`, 'success');
            document.getElementById('contractCode').value = '';
        } else {
            showNotification('Contract deployment failed', 'error');
        }
    } catch (error) {
        showNotification('Network error', 'error');
    }
});

// UI Updates
function updateMiningProgress(data) {
    document.getElementById('hashrate').textContent = `${formatHashrate(data.hashrate)}`;
    document.getElementById('progressBar').style.width = `${data.progress}%`;
}

function handleNewBlock(block) {
    blockHeight = block.index;
    document.getElementById('blockHeight').textContent = `Block #${blockHeight}`;
    
    const blocksTable = document.getElementById('recentBlocks');
    const row = document.createElement('tr');
    
    row.innerHTML = `
        <td class="px-6 py-4 whitespace-nowrap">
            ${block.index}
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            ${block.hash.substring(0, 10)}...
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            ${block.transactions.length}
        </td>
        <td class="px-6 py-4 whitespace-nowrap">
            ${new Date(block.timestamp * 1000).toLocaleString()}
        </td>
    `;
    
    blocksTable.insertBefore(row, blocksTable.firstChild);
    if (blocksTable.children.length > 10) {
        blocksTable.removeChild(blocksTable.lastChild);
    }
    
    showNotification('New block mined', 'success');
}

function updateNetworkStatus(connected) {
    const statusElement = document.getElementById('networkStatus');
    if (connected) {
        statusElement.className = 'text-green-500';
        statusElement.innerHTML = '<i class="fas fa-circle text-xs"></i> Connected';
    } else {
        statusElement.className = 'text-red-500';
        statusElement.innerHTML = '<i class="fas fa-circle text-xs"></i> Disconnected';
    }
}

// Utility Functions
function formatHashrate(hashrate) {
    if (hashrate > 1e9) {
        return `${(hashrate / 1e9).toFixed(2)} GH/s`;
    } else if (hashrate > 1e6) {
        return `${(hashrate / 1e6).toFixed(2)} MH/s`;
    } else if (hashrate > 1e3) {
        return `${(hashrate / 1e3).toFixed(2)} KH/s`;
    }
    return `${hashrate.toFixed(2)} H/s`;
}

function showNotification(message, type) {
    const notifications = document.getElementById('notifications');
    const notification = document.createElement('div');
    
    notification.className = `p-4 rounded-lg shadow-lg ${
        type === 'success' ? 'bg-green-500' : 'bg-red-500'
    } text-white`;
    
    notification.textContent = message;
    notifications.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

// Initialize
initWebSocket();
