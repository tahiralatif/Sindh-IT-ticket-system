/* ═══════════════════════════════════════════════════════════════════
   Chat Widget JS — Conversational AI assistant
   ═══════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    let chatHistory = [];
    let isOpen = false;

    function init() {
        // Inject chat HTML
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

        // Inject CSS
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/css/chat.css';
        document.head.appendChild(link);

        // Toggle open/close
        document.getElementById('chat-toggle').addEventListener('click', toggle);

        // Set role-specific title
        const titleEl = document.getElementById('chat-title');
        const subtitleEl = document.getElementById('chat-subtitle');
        if (typeof IS_ADMIN !== 'undefined' && IS_ADMIN) {
            titleEl.textContent = 'Admin Assistant';
            subtitleEl.textContent = 'Database Query Helper';
        } else {
            titleEl.textContent = 'Ticket Filing Assistant';
            subtitleEl.textContent = 'Government of Sindh';
        }
    }

    function toggle() {
        isOpen = !isOpen;
        const panel = document.getElementById('chat-panel');
        const fab = document.getElementById('chat-toggle');
        panel.classList.toggle('open', isOpen);
        fab.classList.toggle('active', isOpen);

        if (isOpen && chatHistory.length === 0) {
            loadWelcome();
        }
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
        // Simple markdown-like bold
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/\*(.+?)\*/g, '<strong>$1</strong>');
        msg.innerHTML = text;
        container.insertBefore(msg, typingEl);
        container.scrollTop = container.scrollHeight;
    }

    function showTyping() {
        const typing = document.getElementById('chat-typing');
        typing.classList.add('show');
        const container = document.getElementById('chat-messages');
        container.scrollTop = container.scrollHeight;
    }

    function hideTyping() {
        document.getElementById('chat-typing').classList.remove('show');
    }

    async function send() {
        const input = document.getElementById('chat-input');
        const text = input.value.trim();
        if (!text) return;

        input.value = '';
        input.style.height = 'auto';
        addMessage(text, 'user');
        chatHistory.push({ role: 'user', content: text });

        // Disable input while waiting
        const sendBtn = document.getElementById('chat-send');
        input.disabled = true;
        sendBtn.disabled = true;
        showTyping();

        try {
            const formData = new FormData();
            formData.append('message', text);
            formData.append('history_json', JSON.stringify(chatHistory.slice(0, -1)));

            const resp = await fetch('/api/chat', {
                method: 'POST',
                body: formData,
            });
            const data = await resp.json();

            hideTyping();
            if (data.reply) {
                addMessage(data.reply, 'bot');
                chatHistory.push({ role: 'assistant', content: data.reply });
            } else if (data.error) {
                addMessage('Sorry, an error occurred: ' + data.error, 'bot');
            }
        } catch (e) {
            hideTyping();
            addMessage('Connection error. Please try again.', 'bot');
        }

        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
    }

    // Auto-resize textarea
    function autoResize() {
        const input = document.getElementById('chat-input');
        if (input) {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 100) + 'px';
        }
    }

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API
    window.ChatWidget = { send, toggle };

    // Auto-resize listener
    document.addEventListener('input', function (e) {
        if (e.target && e.target.id === 'chat-input') autoResize();
    });
})();
