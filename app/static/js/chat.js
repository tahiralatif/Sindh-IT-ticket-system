/* ═══════════════════════════════════════════════════════════════════
   Chat Widget JS — Conversational AI assistant with write actions
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    let chatHistory = [];
    let isOpen = false;
    let pendingAction = null;  // Stores pending write action for confirmation

    function init() {
        const wrapper = document.createElement('div');
        wrapper.id = 'chat-widget';
        wrapper.innerHTML = `
            <button class="chat-fab" id="chat-toggle" title="AI Assistant">
                <span class="chat-icon">💬</span>
                <span class="chat-close">✕</span>
            </button>
            <div class="chat-panel" id="chat-panel">
                <div class="chat-header">
                    <div class="chat-header-avatar">🤖</div>
                    <div class="chat-header-info">
                        <h3 id="chat-title">AI Assistant</h3>
                        <small id="chat-subtitle">Government of Sindh</small>
                    </div>
                </div>
                <div class="chat-messages" id="chat-messages">
                    <div class="chat-typing" id="chat-typing">
                        <span></span><span></span><span></span>
                    </div>
                </div>
                <div class="chat-input-area">
                    <textarea class="chat-input" id="chat-input"
                        placeholder="Type your message..." rows="1"
                        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();window.ChatWidget.send();}"></textarea>
                    <button class="chat-send" id="chat-send" onclick="window.ChatWidget.send()">
                        <i class="fa fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(wrapper);

        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/css/chat.css';
        document.head.appendChild(link);

        document.getElementById('chat-toggle').addEventListener('click', toggle);

        const titleEl = document.getElementById('chat-title');
        const subtitleEl = document.getElementById('chat-subtitle');
        if (typeof IS_ADMIN !== 'undefined' && IS_ADMIN) {
            titleEl.textContent = 'Admin Assistant';
            subtitleEl.textContent = 'Query & Manage Tickets';
        } else {
            titleEl.textContent = 'Ticket Filing Assistant';
            subtitleEl.textContent = 'Government of Sindh';
        }
    }

    function toggle() {
        isOpen = !isOpen;
        document.getElementById('chat-panel').classList.toggle('open', isOpen);
        document.getElementById('chat-toggle').classList.toggle('active', isOpen);
        if (isOpen && chatHistory.length === 0) loadWelcome();
    }

    async function loadWelcome() {
        showTyping();
        try {
            const resp = await fetch('/api/chat/welcome');
            const data = await resp.json();
            hideTyping();
            addMessage(data.reply, 'bot');
        } catch (e) {
            hideTyping();
            addMessage('Welcome! How can I help you today?', 'bot');
        }
    }

    function addMessage(text, role) {
        const container = document.getElementById('chat-messages');
        const typingEl = document.getElementById('chat-typing');
        const msg = document.createElement('div');
        msg.className = `chat-msg ${role}`;
        // Markdown-like bold
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        msg.innerHTML = text;
        container.insertBefore(msg, typingEl);
        container.scrollTop = container.scrollHeight;
        return msg;
    }

    function showTyping() {
        document.getElementById('chat-typing').classList.add('show');
        document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
    }

    function hideTyping() {
        document.getElementById('chat-typing').classList.remove('show');
    }

    function showConfirmButtons(msgEl) {
        const btnRow = document.createElement('div');
        btnRow.className = 'chat-confirm-row';
        btnRow.innerHTML = `
            <button class="chat-btn chat-btn-confirm" onclick="window.ChatWidget.confirmAction()">✅ Confirm</button>
            <button class="chat-btn chat-btn-cancel" onclick="window.ChatWidget.cancelAction()">❌ Cancel</button>
        `;
        msgEl.parentNode.insertBefore(btnRow, msgEl.nextSibling);
        document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
    }

    function removeConfirmButtons() {
        const rows = document.querySelectorAll('.chat-confirm-row');
        rows.forEach(r => r.remove());
    }

    async function send() {
        const input = document.getElementById('chat-input');
        const text = input.value.trim();
        if (!text) return;

        input.value = '';
        input.style.height = 'auto';
        removeConfirmButtons();
        addMessage(text, 'user');
        chatHistory.push({ role: 'user', content: text });

        const sendBtn = document.getElementById('chat-send');
        input.disabled = true;
        sendBtn.disabled = true;
        showTyping();

        try {
            const formData = new FormData();
            formData.append('message', text);
            formData.append('history_json', JSON.stringify(chatHistory.slice(0, -1)));

            const resp = await fetch('/api/chat', { method: 'POST', body: formData });
            const data = await resp.json();

            hideTyping();
            if (data.reply) {
                const msgEl = addMessage(data.reply, 'bot');
                chatHistory.push({ role: 'assistant', content: data.reply });

                // Check if this response has a pending action
                if (data._pending_action) {
                    pendingAction = data._pending_action;
                    showConfirmButtons(msgEl);
                }
            } else if (data.error) {
                addMessage('Error: ' + data.error, 'bot');
            }
        } catch (e) {
            hideTyping();
            addMessage('Connection error. Please try again.', 'bot');
        }

        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
    }

    async function confirmAction() {
        if (!pendingAction) return;
        removeConfirmButtons();
        addMessage('confirm', 'user');
        chatHistory.push({ role: 'user', content: 'confirm' });

        const sendBtn = document.getElementById('chat-send');
        const input = document.getElementById('chat-input');
        input.disabled = true;
        sendBtn.disabled = true;
        showTyping();

        try {
            const formData = new FormData();
            formData.append('message', 'confirm');
            formData.append('history_json', JSON.stringify(chatHistory.slice(0, -1)));

            const resp = await fetch('/api/chat', { method: 'POST', body: formData });
            const data = await resp.json();

            hideTyping();
            if (data.reply) {
                addMessage(data.reply, 'bot');
                chatHistory.push({ role: 'assistant', content: data.reply });
            }
        } catch (e) {
            hideTyping();
            addMessage('Action failed. Please try again.', 'bot');
        }

        pendingAction = null;
        input.disabled = false;
        sendBtn.disabled = false;
    }

    function cancelAction() {
        removeConfirmButtons();
        pendingAction = null;
        addMessage('cancel', 'user');
        chatHistory.push({ role: 'user', content: 'cancel' });
        addMessage('OK, action cancelled. What else can I help you with?', 'bot');
        chatHistory.push({ role: 'assistant', content: 'OK, action cancelled. What else can I help you with?' });
        document.getElementById('chat-input').focus();
    }

    function autoResize() {
        const input = document.getElementById('chat-input');
        if (input) {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 100) + 'px';
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.ChatWidget = { send, toggle, confirmAction, cancelAction };
    document.addEventListener('input', function (e) {
        if (e.target && e.target.id === 'chat-input') autoResize();
    });
})();
